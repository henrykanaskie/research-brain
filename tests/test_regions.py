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

    # same name for both: the topic is renamed and the region follows it
    regions.rename(s, ["a", "b", "c"], "Alfvén waves", t["slug"], rename_topic=True)
    assert topics.get_topic(s, t["slug"])["name"] == "Alfvén waves"
    out = regions.apply(s, clusters, mem)
    assert out[0]["label"] == "Alfvén waves" and out[0]["name_source"] == "topic"

    # a region without a topic just keeps its name
    regions.rename(s, ["e", "f"], "Laser physics", None, rename_topic=True)
    assert regions.apply(s, clusters, mem)[1]["label"] == "Laser physics"
