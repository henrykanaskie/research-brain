"""Reading status, park-for-later, topics and per-topic reading queues (with mocked Semantic Scholar).

Run:  python3 -m pytest -q tests/test_topics.py
"""
import importlib
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("RB_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("RB_FAKE_CLAUDE", "1")
    monkeypatch.setenv("RB_EMBEDDER", "tfidf")
    monkeypatch.setenv("RB_CITES_ON_IMPORT", "0")
    monkeypatch.setenv("RB_RECS_AUTO", "0")  # tests trigger refreshes explicitly
    for m in [m for m in sys.modules if m.startswith("app")]:
        del sys.modules[m]
    srv = importlib.import_module("app.server")
    monkeypatch.setattr(srv.scholar, "lookup", lambda q: None)
    monkeypatch.setattr(srv.scholar, "s2_paper", lambda pid: None)
    monkeypatch.setattr(srv.citations, "_sleep", lambda s: None)
    monkeypatch.setattr(srv.citations, "MIN_INTERVAL", 0.0)
    return srv


@pytest.fixture()
def client(server):
    from fastapi.testclient import TestClient
    return TestClient(server.app)


def paper(client, title, abstract="", s2_id="", **kw):
    r = client.post("/api/notes", json={"type": "paper", "title": title, "abstract": abstract, **kw})
    assert r.status_code == 201, r.text
    nid = r.json()["id"]
    if s2_id:
        # s2_id isn't user-editable through PUT; set it like an import would
        from app import server as srv  # noqa: F401  (the fixture's fresh module)
        sys.modules["app.server"].store.set_fields(nid, s2_id=s2_id)
    return nid


# ---------------------------------------------------------------- status
def test_status_defaults_and_validation(client, server):
    old = client.post("/api/notes", json={"type": "note", "title": "Idea"}).json()
    assert old["status"] == "" and old["saved_because"] == ""
    p = client.post("/api/notes", json={"type": "paper", "title": "Hall thrusters"}).json()
    assert p["status"] == "to-read"  # new papers wait to be read
    r = client.put(f"/api/notes/{p['id']}", json={"status": "reading"})
    assert r.status_code == 200 and r.json()["status"] == "reading"
    assert client.put(f"/api/notes/{p['id']}", json={"status": "skimmed"}).status_code == 400
    # a file written before this feature (no status key) reads as ""
    path = server.store.note_path(p["id"])
    raw = path.read_text(encoding="utf-8").replace("status: reading\n", "")
    path.write_text(raw)
    assert client.get(f"/api/notes/{p['id']}").json()["status"] == ""
    # the meta index mirrors the field (graph nodes carry it)
    client.put(f"/api/notes/{p['id']}", json={"status": "read"})
    nodes = {n["id"]: n for n in client.get("/api/graph").json()["nodes"]}
    assert nodes[p["id"]]["status"] == "read"


def test_meta_index_rebuilds_for_new_fields(server, tmp_path):
    # FIELDS_DEFAULTS changed -> a db built from older fields is rebuilt (fields signature)
    meta = server.store.meta
    assert meta.ok
    meta.conn.execute("UPDATE info SET v='old' WHERE k='fields'")
    from app.meta import MetaIndex
    m2 = MetaIndex(server.store)
    assert m2.ok and m2.rebuilds >= 1


def test_park_for_later(client):
    nid = paper(client, "Sheath physics")
    r = client.post(f"/api/notes/{nid}/park", json={"saved_because": "  Needed when\nI model the wall sheath  "})
    assert r.status_code == 200
    n = r.json()
    assert n["status"] == "later"
    assert n["saved_because"] == "Needed when I model the wall sheath"  # one line
    # park without a reason keeps the existing one
    client.put(f"/api/notes/{nid}", json={"status": "reading"})
    n = client.post(f"/api/notes/{nid}/park").json()
    assert n["status"] == "later" and n["saved_because"].startswith("Needed when")


# ---------------------------------------------------------------- topics CRUD + files
def test_topic_crud_and_membership(client, server):
    a, b = paper(client, "Erosion of channel walls"), paper(client, "Magnetic shielding")
    r = client.post("/api/topics", json={"name": "Hall thruster lifetime", "description": "wall erosion"})
    assert r.status_code == 201
    t = r.json()
    slug = t["slug"]
    assert slug == "hall-thruster-lifetime" and t["count"] == 0
    assert client.post("/api/topics", json={"name": "hall thruster LIFETIME"}).status_code == 400
    assert client.post("/api/topics", json={"name": "  "}).status_code == 400

    t = client.post(f"/api/topics/{slug}/notes", json={"ids": [a, b, a]}).json()
    assert t["members"] == [a, b] and t["counts"]["to-read"] == 2
    client.put(f"/api/notes/{b}", json={"status": "read"})
    t = client.get(f"/api/topics/{slug}").json()
    groups = {g["status"]: g for g in t["groups"]}
    assert groups["to-read"]["count"] == 1 and groups["read"]["items"][0]["id"] == b
    assert [g["label"] for g in t["groups"]][:4] == ["To read", "Reading", "Later", "Read"]

    # the note knows its topics
    assert client.get(f"/api/notes/{a}").json()["topics"] == [{"slug": slug, "name": "Hall thruster lifetime"}]
    # fragments can't be members; unknown ids are refused
    f = client.post("/api/notes", json={"type": "fragment", "parent": a, "quote": "q"}).json()["id"]
    assert client.post(f"/api/topics/{slug}/notes", json={"ids": [f]}).status_code == 400
    assert client.post(f"/api/topics/{slug}/notes", json={"ids": ["nope"]}).status_code == 400

    # rename keeps the slug (links stay stable)
    t = client.patch(f"/api/topics/{slug}", json={"name": "Thruster lifetime"}).json()
    assert t["name"] == "Thruster lifetime" and t["slug"] == slug
    lst = client.get("/api/topics").json()
    assert lst[0]["count"] == 2 and lst[0]["counts"]["read"] == 1

    t = client.delete(f"/api/topics/{slug}/notes/{a}")
    assert t.json()["members"] == [b]
    assert client.delete(f"/api/topics/{slug}").json() == {"ok": True}
    assert client.get(f"/api/topics/{slug}").status_code == 404
    assert client.get("/api/topics").json() == []
    assert any(p.name.startswith(f"topic-{slug}") for p in server.store.trash_dir.iterdir())


def test_topic_file_persistence(client, server):
    a = paper(client, "Plasma sheath")
    slug = client.post("/api/topics", json={"name": "Sheaths", "members": [a]}).json()["slug"]
    path = server.store.root / "topics" / f"{slug}.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\nname: Sheaths\n") and f"- [[{a}]]" in text
    # hand-edit (e.g. in Obsidian): add a member line and a description
    b = paper(client, "Bohm criterion")
    path.write_text(text.replace("description: ''", "description: hand written") + f"- [[{b}|Bohm]]\n")
    t = client.get(f"/api/topics/{slug}").json()
    assert t["members"] == [a, b] and t["description"] == "hand written"
    # dismissals and snoozes live in the topic file, not in .index (which is safe to delete)
    client.post(f"/api/topics/{slug}/queue/dismiss", json={"s2_id": "X1", "title": "Bad paper"})
    client.post(f"/api/topics/{slug}/queue/snooze", json={"key": "X2"})
    import shutil
    if server.store.meta is not None:
        server.store.meta._close()  # Windows can't delete a file another connection holds open
    shutil.rmtree(server.store.index_dir)
    server.store.index_dir.mkdir()
    t = server.topics_mod.get_topic(server.store, slug)
    assert t["dismissed"][0]["s2_id"] == "X1" and "X2" in t["snoozed"]


def test_region_to_topic(client, server):
    ids = [paper(client, f"Hall thruster erosion study {i}", "hall thruster channel wall erosion boron nitride")
           for i in range(3)]
    ids += [paper(client, f"Tokamak disruption {i}", "tokamak disruption prediction neural network") for i in range(3)]
    sug = client.get("/api/topics/suggest").json()
    assert sug, "regions should be suggested"
    s = sug[0]
    assert s["name"] and s["name"][0].isupper() and len(s["members"]) >= 2
    # one click: create a topic from it (name editable, members pre-filled)
    t = client.post("/api/topics", json={"name": s["name"] + " (mine)", "members": s["members"]}).json()
    assert set(t["members"]) == set(s["members"])
    # that region is no longer suggested
    assert all(x["region"] != s["region"] for x in client.get("/api/topics/suggest").json())


def test_topic_functions_for_other_modules(client, server):
    a, b = paper(client, "A paper"), paper(client, "B paper")
    client.post(f"/api/notes/{b}/park", json={"saved_because": "later use"})
    slug = client.post("/api/topics", json={"name": "T", "members": [a, b]}).json()["slug"]
    tm = server.topics_mod
    assert [x["id"] for x in tm.papers_by_status(server.store, "later")] == [b]
    assert [x["id"] for x in tm.papers_by_status(server.store, "to-read", topic=slug)] == [a]
    assert tm.topics_for(server.store, a)[0]["slug"] == slug
    assert tm.list_topics(server.store)[0]["counts"]["later"] == 1


# ---------------------------------------------------------------- queue
def test_queue_orders_to_read_by_relevance(client, server):
    read = paper(client, "Hall thruster wall erosion", "boron nitride channel wall erosion sputtering yield")
    client.put(f"/api/notes/{read}", json={"status": "read"})
    far = paper(client, "Protein folding", "amino acid chains fold into structures")
    near = paper(client, "Sputtering of boron nitride walls", "channel wall erosion sputtering yield measurements")
    cited = paper(client, "Ion energy distributions", "retarding potential analyzer ion energy")
    server.store.set_fields(read, cites=[cited])
    slug = client.post("/api/topics", json={"name": "Erosion", "members": [read, far, near, cited]}).json()["slug"]
    q = client.get(f"/api/topics/{slug}/queue").json()
    ids = [i["id"] for i in q["items"]]
    assert set(ids) == {far, near, cited} and q["to_read"] == 3
    assert ids.index(near) < ids.index(far)
    assert ids.index(cited) < ids.index(far)
    by = {i["id"]: i for i in q["items"]}
    assert by[cited]["reason"] == "Cited by 1 paper in this topic"
    assert by[near]["reason"] == "Similar to Hall thruster wall erosion"
    # snooze a library paper ("Not now")
    client.post(f"/api/topics/{slug}/queue/snooze", json={"key": f"note:{far}"})
    assert far not in [i["id"] for i in client.get(f"/api/topics/{slug}/queue").json()["items"]]


def _mock_s2(server, monkeypatch, refs: dict, recs: list, calls: dict):
    def references(key, citing=False, max_items=1000):
        calls.setdefault("refs", []).append(key)
        return refs.get(key, [])

    def recommend(pos, neg=None, limit=60):
        calls.setdefault("recs", []).append({"pos": list(pos), "neg": list(neg or [])})
        return [dict(r) for r in recs]
    monkeypatch.setattr(server.scholar, "references", references)
    monkeypatch.setattr(server.scholar, "recommend_for_papers", recommend)


def _rec(s2, title, abstract=""):
    return {"s2_id": s2, "title": title, "year": 2021, "authors": ["A. Author"], "abstract": abstract,
            "doi": "", "arxiv": "", "venue": "", "url": "", "citationCount": 3}


def _topic_with_papers(client):
    p1 = paper(client, "Hall thruster erosion", "channel wall erosion sputtering", s2_id="P1")
    p2 = paper(client, "Magnetic shielding", "magnetic shielding reduces wall erosion", s2_id="P2")
    p3 = paper(client, "Anomalous transport", "electron transport bohm", s2_id="P3")
    for p in (p1, p2, p3):
        client.put(f"/api/notes/{p}", json={"status": "read"})
    slug = client.post("/api/topics", json={"name": "Erosion", "members": [p1, p2, p3]}).json()["slug"]
    return slug, (p1, p2, p3)


def test_recommendation_fusion(client, server, monkeypatch):
    slug, (p1, p2, p3) = _topic_with_papers(client)
    shared = {"s2_id": "C2", "title": "Sputtering yields of boron nitride", "doi": "", "arxiv": ""}
    once = {"s2_id": "C1", "title": "Only cited once", "doi": "", "arxiv": ""}
    in_lib = {"s2_id": "P3", "title": "Anomalous transport", "doi": "", "arxiv": ""}
    refs = {"P1": [shared, once, in_lib], "P2": [shared, in_lib], "P3": [shared]}
    recs = [_rec("R1", "Wall erosion modelling with machine learning", "channel wall erosion sputtering model"),
            _rec("R2", "Unrelated cooking paper", "recipes"),
            _rec("C2", "Sputtering yields of boron nitride", "sputtering boron nitride erosion")]
    calls = {}
    _mock_s2(server, monkeypatch, refs, recs, calls)
    r = client.post(f"/api/topics/{slug}/queue/refresh?wait=1")
    assert r.status_code == 200
    q = r.json()
    items = q["items"]
    assert q["recs_state"] == "fresh" and len(items) <= 5
    keys = [i["s2_id"] for i in items]
    assert keys[0] == "C2"  # in all three lists: cited by 3, recommended, similar
    assert "C1" not in keys and "P3" not in keys  # cited once only / already in the library
    assert "R1" in keys and keys.index("R1") < keys.index("R2")
    c2 = items[0]
    assert c2["reason"] == "Cited by 3 papers in this topic" and set(c2["sources"]) == {"cited", "s2", "similar"}
    r1 = next(i for i in items if i["s2_id"] == "R1")
    assert r1["reason"].startswith("Similar to ")
    assert sorted(calls["recs"][0]["pos"]) == ["P1", "P2", "P3"] and calls["recs"][0]["neg"] == []
    # references were cached with a TTL: a second refresh doesn't refetch them
    cache = json.loads((server.store.index_dir / "refs-cache.json").read_text(encoding="utf-8"))
    assert set(cache) == {"P1", "P2", "P3"} and all("at" in v for v in cache.values())
    n = len(calls["refs"])
    client.post(f"/api/topics/{slug}/queue/refresh?wait=1")
    assert len(calls["refs"]) == n
    # reading the queue is instant: it serves the cache and never calls S2 itself
    m = len(calls["recs"])
    assert client.get(f"/api/topics/{slug}/queue").json()["items"][0]["s2_id"] == "C2"
    assert len(calls["recs"]) == m


def test_dismiss_feeds_negatives_and_is_never_shown(client, server, monkeypatch):
    slug, papers = _topic_with_papers(client)
    recs = [_rec("R1", "Wall erosion modelling"), _rec("R2", "Second recommendation")]
    calls = {}
    _mock_s2(server, monkeypatch, {}, recs, calls)
    client.post(f"/api/topics/{slug}/queue/refresh?wait=1")
    assert client.post(f"/api/topics/{slug}/queue/dismiss", json={"s2_id": "R1"}).status_code == 200
    q = client.get(f"/api/topics/{slug}/queue").json()
    assert [i["s2_id"] for i in q["items"]] == ["R2"]  # hidden immediately from the cache
    client.post(f"/api/topics/{slug}/queue/refresh?wait=1")
    assert calls["recs"][-1]["neg"] == ["R1"]
    assert "R1" not in [i["s2_id"] for i in client.get(f"/api/topics/{slug}/queue").json()["items"]]
    text = (server.store.root / "topics" / f"{slug}.md").read_text(encoding="utf-8")
    assert "dismissed:" in text and "R1" in text and "Wall erosion modelling" in text


def test_snooze_hides_for_14_days(client, server, monkeypatch):
    slug, _ = _topic_with_papers(client)
    _mock_s2(server, monkeypatch, {}, [_rec("R1", "Wall erosion modelling")], {})
    client.post(f"/api/topics/{slug}/queue/refresh?wait=1")
    r = client.post(f"/api/topics/{slug}/queue/snooze", json={"key": "R1"}).json()
    import datetime as dt
    until = dt.datetime.fromisoformat(r["until"])
    assert 13.9 < (until - dt.datetime.now().astimezone()).total_seconds() / 86400 < 14.1
    assert client.get(f"/api/topics/{slug}/queue").json()["items"] == []
    # once the snooze expires it comes back (and the expired entry is dropped from the file on next write)
    t = server.topics_mod.get_topic(server.store, slug)
    t["snoozed"]["R1"] = (dt.datetime.now().astimezone() - dt.timedelta(minutes=1)).isoformat()
    server.topics_mod._write(server.store, t)
    assert [i["s2_id"] for i in client.get(f"/api/topics/{slug}/queue").json()["items"]] == ["R1"]


def test_offline_fallback(client, server, monkeypatch):
    slug, (p1, p2, p3) = _topic_with_papers(client)
    todo = paper(client, "Erosion measurements", "erosion profilometry")
    client.post(f"/api/topics/{slug}/notes", json={"ids": [todo]})

    def offline(*a, **k):
        raise server.scholar.ScholarError("Couldn't reach api.semanticscholar.org (ConnectionError). Check your internet connection.")
    monkeypatch.setattr(server.scholar, "references", offline)
    monkeypatch.setattr(server.scholar, "recommend_for_papers", offline)
    q = client.post(f"/api/topics/{slug}/queue/refresh?wait=1").json()
    assert [i["id"] for i in q["items"]] == [todo]  # just the to-read papers
    assert q["recs_state"] == "offline" and q["note"] == "Offline. Showing only your to-read papers."
    # with an earlier cache, offline shows the cached recommendations with a quiet note
    _mock_s2(server, monkeypatch, {}, [_rec("R1", "Wall erosion modelling")], {})
    server.topics_mod.refresh_recs(server.store, server.index, server.embedder, slug)
    monkeypatch.setattr(server.scholar, "recommend_for_papers", offline)
    monkeypatch.setattr(server.scholar, "references", offline)
    q = client.post(f"/api/topics/{slug}/queue/refresh?wait=1").json()
    assert [i.get("id") or i["s2_id"] for i in q["items"]] == [todo, "R1"]
    assert q["recs_state"] == "offline" and q["note"].startswith("Offline. Showing recommendations from")


def test_accept_imports_into_topic(client, server, monkeypatch):
    slug, _ = _topic_with_papers(client)
    _mock_s2(server, monkeypatch, {}, [_rec("R1", "Wall erosion modelling", "erosion model")], {})
    client.post(f"/api/topics/{slug}/queue/refresh?wait=1")
    monkeypatch.setattr(server.scholar, "s2_paper", lambda pid: _rec(pid, "Wall erosion modelling", "erosion model"))
    r = client.post(f"/api/topics/{slug}/queue/accept", json={"s2_id": "R1"})
    assert r.status_code == 201, r.text
    n = r.json()
    assert n["type"] == "paper" and n["s2_id"] == "R1" and n["status"] == "to-read"
    assert n["topics"] == [{"slug": slug, "name": "Erosion"}]
    q = client.get(f"/api/topics/{slug}/queue").json()
    assert [i.get("id") for i in q["items"]] == [n["id"]]  # now a to-read paper, no longer a recommendation
    # offline accept falls back to the cached metadata
    _mock_s2(server, monkeypatch, {}, [_rec("R9", "Another one")], {})
    client.post(f"/api/topics/{slug}/queue/refresh?wait=1")

    def offline(pid):
        raise server.scholar.ScholarError("Couldn't reach api.semanticscholar.org")
    monkeypatch.setattr(server.scholar, "s2_paper", offline)
    r = client.post(f"/api/topics/{slug}/queue/accept", json={"s2_id": "R9"})
    assert r.status_code == 201 and r.json()["title"] == "Another one"


def test_global_queue(client, server, monkeypatch):
    # no topics: the library's to-read papers
    ids = [paper(client, f"Paper {i}") for i in range(7)]
    q = client.get("/api/queue").json()
    assert q["total"] == 7 and len(q["items"]) == 5
    assert len(client.get("/api/queue?offset=5").json()["items"]) == 2
    assert client.get("/api/queue?limit=50").json()["limit"] == 5  # never more than 5 at once
    # topics: round-robin, tagged with the topic
    t1 = client.post("/api/topics", json={"name": "One", "members": ids[:3]}).json()["slug"]
    client.post("/api/topics", json={"name": "Two", "members": ids[3:5]})
    q = client.get("/api/queue").json()
    assert [i["topic"]["name"] for i in q["items"]][:4] == ["One", "Two", "One", "Two"]
    assert q["total"] == 5
    assert t1


def test_recommend_for_papers_request_shape(server, monkeypatch):
    """The HTTP call matches the documented API: POST /recommendations/v1/papers with
    {positivePaperIds, negativePaperIds}, fields+limit as query params, response recommendedPapers."""
    seen = {}

    class R:
        status_code = 200
        text = ""

        def json(self):
            return {"recommendedPapers": [{"paperId": "abc", "title": "T", "year": 2020, "authors": [{"name": "X"}],
                                           "externalIds": {"DOI": "10.1/x"}, "abstract": "a"},
                                          {"paperId": "nope"}]}

    def post(url, params=None, json=None, headers=None, timeout=None):
        seen.update(url=url, params=params, json=json)
        return R()
    monkeypatch.setattr(server.scholar.requests, "post", post)
    out = server.scholar.recommend_for_papers(["P1", "P1", "P2"], ["N1"], limit=10)
    assert seen["url"] == "https://api.semanticscholar.org/recommendations/v1/papers"
    assert seen["json"] == {"positivePaperIds": ["P1", "P2"], "negativePaperIds": ["N1"]}
    assert seen["params"]["limit"] == 10 and "abstract" in seen["params"]["fields"]
    assert out == [server.scholar._paper_from_s2(R().json()["recommendedPapers"][0])]
