"""Region names (app/regions.py): yours, else the matching topic's, else automatic."""
from types import SimpleNamespace

from app import regions, topics


def _store(tmp_path):
    s = SimpleNamespace(root=tmp_path, docs=tmp_path / "docs")
    s.all = lambda: [{"id": i, "type": "paper"} for i in "abcdefgh"]
    s.get = lambda i: {"id": i, "type": "paper"}
    return s


def test_topic_name_then_custom_then_reset(tmp_path):
    s = _store(tmp_path)
    t = topics.create_topic(s, "Plasma waves", members=["a", "b", "c"])
    clusters = [{"id": 0, "label": "alfven, wave", "size": 3}, {"id": 1, "label": "lasers", "size": 2}]
    mem = {0: ["a", "b", "c"], 1: ["e", "f"]}
    out = regions.apply(s, clusters, mem)
    assert out[0]["label"] == "Plasma waves" and out[0]["name_source"] == "topic"
    assert out[0]["topic"]["slug"] == t["slug"]
    assert out[1]["label"] == "lasers" and out[1]["name_source"] == "auto" and out[1]["topic"] is None

    # keep them different: the region gets its own name, the topic keeps its own
    regions.rename(s, ["a", "b", "c"], "Waves", t["slug"], rename_topic=False)
    out = regions.apply(s, clusters, mem)
    assert out[0]["label"] == "Waves" and out[0]["name_source"] == "custom"
    assert topics.get_topic(s, t["slug"])["name"] == "Plasma waves"

    # a slightly different membership is still the same region
    out = regions.apply(s, clusters, {0: ["a", "b", "c", "d"], 1: ["e", "f"]})
    assert out[0]["label"] == "Waves"

    regions.reset(s, ["a", "b", "c"])
    assert regions.apply(s, clusters, mem)[0]["label"] == "Plasma waves"

    # same name for both: the topic is renamed, and the region keeps the name as its own too
    regions.rename(s, ["a", "b", "c"], "Alfvén waves", t["slug"], rename_topic=True)
    assert topics.get_topic(s, t["slug"])["name"] == "Alfvén waves"
    out = regions.apply(s, clusters, mem)
    assert out[0]["label"] == "Alfvén waves" and out[0]["name_source"] == "custom"

    # a region without a topic just keeps its name
    regions.rename(s, ["e", "f"], "Laser physics", None, rename_topic=True)
    assert regions.apply(s, clusters, mem)[1]["label"] == "Laser physics"


def test_your_name_follows_the_region_and_beats_ai_and_topics(tmp_path):
    s = _store(tmp_path)
    clusters = [{"id": 0, "label": "cross, section", "size": 4}, {"id": 1, "label": "crm", "size": 3}]
    regions.save_ai_names(s, [(["a", "b", "c", "d"], "Atomic Cross Sections"), (["e", "f", "g"], "Collisional-Radiative Models")])
    out = regions.apply(s, clusters, {0: ["a", "b", "c", "d"], 1: ["e", "f", "g"]})
    assert [o["label"] for o in out] == ["Atomic Cross Sections", "Collisional-Radiative Models"]
    assert out[0]["name_source"] == "ai"
    regions.rename(s, ["e", "f", "g"], "My CRM work", None, rename_topic=False)
    topics.create_topic(s, "Some topic", members=["e", "f", "g"])
    # the region grows and loses a paper, step by step: the name stays with it
    for mem in (["e", "f", "g", "h"], ["f", "g", "h"], ["f", "g", "h", "a", "b"]):
        out = regions.apply(s, [clusters[1]], {1: mem})
        assert out[0]["label"] == "My CRM work" and out[0]["name_source"] == "custom"
    # a region that only brushes against it doesn't take it
    out = regions.apply(s, [{"id": 5, "label": "x", "size": 6}], {5: ["h", "c", "d", "x1", "x2", "x3"]})
    assert out[0]["name_source"] != "custom"


def test_ai_names_regions_from_titles_without_private_ones(tmp_path, monkeypatch):
    import importlib
    import sys
    monkeypatch.setenv("RB_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("RB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RB_EMBEDDER", "tfidf")
    for m in [m for m in sys.modules if m.startswith("app")]:
        del sys.modules[m]
    srv = importlib.import_module("app.server")
    from test_questions import Recorder
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
    srv.regions_mod.rename(srv.store, ids, "Mine", None, rename_topic=False)
    out = srv.regions_mod.apply(srv.store, [{"id": 0, "label": "cross sections", "size": 3}], {0: ids})
    assert out[0]["label"] == "Mine"
