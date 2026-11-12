"""[[wikilinks]] + backlinks (Obsidian-style).

Run:  .venv/bin/python -m pytest -q tests/test_wikilinks.py
"""
import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RB_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("RB_FAKE_CLAUDE", "1")
    monkeypatch.setenv("RB_EMBEDDER", "tfidf")
    for m in [m for m in sys.modules if m.startswith("app")]:
        del sys.modules[m]
    server = importlib.import_module("app.server")
    from fastapi.testclient import TestClient
    return TestClient(server.app)


def mk(client, **fields):
    fields.setdefault("type", "note")
    r = client.post("/api/notes", json=fields)
    assert r.status_code == 201, r.text
    return r.json()


def get(client, nid):
    r = client.get(f"/api/notes/{nid}")
    assert r.status_code == 200, r.text
    return r.json()


def test_resolve_by_id_title_and_alias(client):
    attn = mk(client, type="paper", title="Attention Is All You Need", year=2017)
    sheath = mk(client, title="Plasma sheath")
    src = mk(client, title="Idea log",
             summary=f"Builds on [[{attn['id']}]].",
             body="Compare with [[plasma SHEATH]] and [[Plasma sheath|the sheath note]]. Also [[Nowhere yet]]. "
                  "`[[not a link]]` stays code.")
    n = get(client, src["id"])
    assert [m["id"] for m in n["mentions"]] == [attn["id"], sheath["id"]]  # deduped, in order
    assert n["mentions"][0] == {"id": attn["id"], "title": attn["title"], "type": "paper"}
    assert n["unresolved"] == ["Nowhere yet"]
    # nothing is stored as a manual link
    assert n["links"] == []
    assert get(client, attn["id"])["mentions"] == [] and get(client, attn["id"])["unresolved"] == []


def test_title_prefers_non_fragment(client):
    paper = mk(client, type="paper", title="Dispersion")
    other = mk(client, title="Host")
    frag = mk(client, type="fragment", parent=other["id"], title="Dispersion", quote="w(k)")
    src = mk(client, title="Uses it", body="see [[dispersion]]")
    assert [m["id"] for m in get(client, src["id"])["mentions"]] == [paper["id"]]
    # a fragment is reachable by its id, and fragments can link out from their quote
    src2 = mk(client, title="Uses frag", body=f"see [[{frag['id']}]]")
    assert [m["id"] for m in get(client, src2["id"])["mentions"]] == [frag["id"]]
    client.put(f"/api/notes/{frag['id']}", json={"quote": "w(k), cf. [[Uses it]]"})
    assert [m["id"] for m in get(client, frag["id"])["mentions"]] == [src["id"]]


def test_backlinks_with_snippet(client):
    tgt = mk(client, title="Debye length")
    long_before = "Lorem ipsum dolor sit amet " * 12
    src = mk(client, title="Sheath notes",
             body=long_before + "the sheath thickness scales with [[Debye length|$\\lambda_D$]] in a few units. "
                  + "More words follow here " * 10)
    mk(client, title="Unrelated", body="no links")
    bl = get(client, tgt["id"])["backlinks"]
    assert len(bl) == 1
    b = bl[0]
    assert b["id"] == src["id"] and b["title"] == "Sheath notes" and b["type"] == "note"
    assert "[[Debye length|$\\lambda_D$]]" in b["snippet"]  # brackets kept
    assert b["snippet"].startswith("…") and b["snippet"].endswith("…")
    assert 100 <= len(b["snippet"]) <= 220, len(b["snippet"])
    # self-links are ignored
    client.put(f"/api/notes/{tgt['id']}", json={"body": "I am [[Debye length]]"})
    assert get(client, tgt["id"])["mentions"] == []


def test_graph_mention_edges(client):
    a = mk(client, title="Alpha note")
    b = mk(client, title="Beta note")
    c = mk(client, title="Gamma note")
    mk(client, title="Writer", body=f"[[Alpha note]] twice: [[alpha note]] and [[{a['id']}|A]]; [[Beta note]]")
    writer = "writer"
    # manual link writer<->beta exists: no mention edge for that pair
    client.post(f"/api/notes/{writer}/link", json={"target": b["id"]})
    # c mentions writer, and writer mentions... nothing back: one edge
    client.put(f"/api/notes/{c['id']}", json={"summary": "[[Writer]]"})
    g = client.get("/api/graph").json()
    mentions = [(l["source"], l["target"]) for l in g["links"] if l["kind"] == "mention"]
    assert sorted(mentions) == sorted([(writer, a["id"]), (c["id"], writer)])
    manual = [l for l in g["links"] if l["kind"] == "manual"]
    assert len(manual) == 1
    deg = {n["id"]: n["degree"] for n in g["nodes"]}
    assert deg[writer] == 3 and deg[a["id"]] == 1 and deg[b["id"]] == 1 and deg[c["id"]] == 1
    # reciprocal mentions dedupe to one edge
    client.put(f"/api/notes/{a['id']}", json={"body": "back to [[Writer]]"})
    g = client.get("/api/graph").json()
    pairs = [tuple(sorted((l["source"], l["target"]))) for l in g["links"] if l["kind"] == "mention"]
    assert len(pairs) == len(set(pairs)) == 2


def test_create_from_unresolved(client):
    src = mk(client, title="Draft", body="Need a note on [[Ohmic heating]].")
    assert get(client, src["id"])["unresolved"] == ["Ohmic heating"]
    new = mk(client, title="Ohmic heating")
    s = get(client, src["id"])
    assert s["unresolved"] == [] and [m["id"] for m in s["mentions"]] == [new["id"]]
    assert [b["id"] for b in get(client, new["id"])["backlinks"]] == [src["id"]]


def test_rename_rewrites_title_links(client):
    tgt = mk(client, title="Breathing mode")
    by_title = mk(client, title="Uses title",
                  body="See [[breathing MODE]] and [[Breathing mode|the oscillation]]. `[[Breathing mode]]` is code.",
                  summary="[[Breathing mode]]")
    by_id = mk(client, title="Uses id", body=f"See [[{tgt['id']}]].")
    other = mk(client, title="Other", body="[[Something else]]")
    r = client.put(f"/api/notes/{tgt['id']}", json={"title": "Breathing-mode oscillation"})
    assert r.status_code == 200
    assert r.json()["rewritten"] == [by_title["id"]]
    t = get(client, by_title["id"])
    assert t["body"] == ("See [[Breathing-mode oscillation]] and [[Breathing-mode oscillation|the oscillation]]. "
                         "`[[Breathing mode]]` is code.")
    assert t["summary"] == "[[Breathing-mode oscillation]]"
    assert get(client, by_id["id"])["body"] == f"See [[{tgt['id']}]]."
    assert get(client, other["id"])["body"] == "[[Something else]]"
    bl = {b["id"] for b in get(client, tgt["id"])["backlinks"]}
    assert bl == {by_title["id"], by_id["id"]}
    # renaming onto a title another note already owns: links switch to [[id|text]] so they still point here
    taken = mk(client, title="Taken")
    r = client.put(f"/api/notes/{tgt['id']}", json={"title": "Taken"})
    assert r.json()["rewritten"] == [by_title["id"]]
    t = get(client, by_title["id"])
    assert t["summary"] == f"[[{tgt['id']}|Breathing-mode oscillation]]"
    assert f"[[{tgt['id']}|the oscillation]]" in t["body"]
    assert [b["id"] for b in get(client, taken["id"])["backlinks"]] == []
    assert {b["id"] for b in get(client, tgt["id"])["backlinks"]} == {by_title["id"], by_id["id"]}
    # id links survive any later rename untouched
    r = client.put(f"/api/notes/{tgt['id']}", json={"title": "Final name"})
    assert "rewritten" not in r.json()
    # a body-only update never rewrites
    assert "rewritten" not in client.put(f"/api/notes/{other['id']}", json={"body": "x"}).json()


def test_rename_skips_ambiguous_title(client):
    a = mk(client, title="Same name")
    b = mk(client, title="Same name")
    src = mk(client, title="Ref", body="[[Same name]]")
    assert [m["id"] for m in get(client, src["id"])["mentions"]] == [a["id"]]  # oldest wins
    # renaming the note the link does NOT resolve to leaves it alone
    r = client.put(f"/api/notes/{b['id']}", json={"title": "Second"})
    assert "rewritten" not in r.json() and get(client, src["id"])["body"] == "[[Same name]]"


def test_unlink_a_mention_keeps_the_words(client):
    """Right-click Unlink on a [[mention]]: the links to that note become plain text; links to other notes,
    and anything inside code, stay as they are."""
    target = mk(client, title="Helium line ratios")
    other = mk(client, title="Stochastic parrots")
    src = mk(client, title="Cross sections", body=(
        "See [[Helium line ratios]] and [[" + target["id"] + "|the helium paper]], not [[Stochastic parrots]].\n"
        "Code keeps it: `[[Helium line ratios]]`"))
    links = lambda: {(l["source"], l["target"], l["kind"]) for l in client.get("/api/graph").json()["links"]}
    assert (src["id"], target["id"], "mention") in links()
    r = client.post(f"/api/notes/{src['id']}/unmention", json={"target": target["id"]})
    assert r.status_code == 200 and r.json()["unmentioned"] == ["body"]
    body = client.get(f"/api/notes/{src['id']}").json()["body"]
    assert body.startswith("See Helium line ratios and the helium paper, not [[Stochastic parrots]].")
    assert "`[[Helium line ratios]]`" in body
    assert (src["id"], target["id"], "mention") not in links() and (src["id"], other["id"], "mention") in links()
    again = client.post(f"/api/notes/{src['id']}/unmention", json={"target": target["id"]}).json()
    assert again["unmentioned"] == []
