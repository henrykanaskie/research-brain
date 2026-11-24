"""The same paper twice (app/duplicates.py): a preprint and its journal version whose ids don't match. Found by
title (math and punctuation set aside), authors in any order and year; adding it again by DOI gives the one you
have; the paper's panel offers Merge, which keeps this one's PDF and puts the other aside; or Not the same."""
from pathlib import Path

from app import duplicates
from test_api import client, make_pdf  # noqa: F401  (fixture)

PRE = r"Cross-field chaotic transport of electrons by $\vec E \times \vec B$ electron drift instability in Hall thrusters"
PUB = "Cross-field chaotic transport of electrons by E × B electron drift instability in Hall thruster"
AUTHORS = ["Debraj Mandal", "Yves Elskens", "Nicolas Lemoine", "Fabrice Doveil"]


def _paper(**kw):
    return {"id": kw.pop("id", kw.get("title", "")[:10]), "type": "paper", **kw}


def test_what_counts_as_the_same_paper():
    a = _paper(id="a", title=PRE, authors=AUTHORS, year=2019)
    assert duplicates.same_paper(a, _paper(id="b", title=PUB, authors=["D. Mandal", "Y. Elskens", "N. Lemoine", "F. Doveil"], year=2020))
    assert duplicates.same_paper(a, _paper(id="c", title=PUB, authors=list(reversed(AUTHORS)), year=2020))  # any order
    assert duplicates.same_paper(a, _paper(id="d", title=PUB, authors=["Mandal, Debraj", "Elskens, Yves"], year=2019))
    assert duplicates.same_paper(a, _paper(id="e", title=PUB, authors=[], year=None))  # no authors on one side
    assert not duplicates.same_paper(a, _paper(id="f", title=PUB, authors=["A. Smith", "B. Jones"], year=2020))
    assert not duplicates.same_paper(a, _paper(id="g", title=PUB, authors=AUTHORS, year=2015))  # years apart
    # a series: Part 1 / Part 2 (numbers differ), and I / II with the rest of the title different
    s1 = _paper(id="s1", title="Collisional radiative model of xenon plasma, part 1: cross sections", authors=AUTHORS, year=2019)
    assert not duplicates.same_paper(s1, _paper(id="s2", title="Collisional radiative model of xenon plasma, part 2: cross sections", authors=AUTHORS, year=2019))
    x1 = _paper(id="x1", title="A xenon collisional-radiative model applicable to electric propulsion devices: I. Calculations of "
                                 "electron-impact cross sections for xenon ions by the Dirac B-spline R-matrix method",
                authors=["Yang Wang", "Yan-Fei Wang", "Xi-Ming Zhu"], year=2019)
    x2 = _paper(id="x2", title="A xenon collisional-radiative model applicable to electric propulsion devices: II. Kinetics of the "
                                 "6 s , 6 p , and 5 d states of atoms and ions in Hall thrusters",
                authors=["Xi-Ming Zhu", "Yan-Fei Wang", "Yang Wang"], year=2019)
    assert not duplicates.same_paper(x1, x2)
    assert not duplicates.same_paper(a, _paper(id="n", title="Short title", authors=AUTHORS, year=2019))
    assert not duplicates.same_paper(a, {**a})  # itself


def _ids(store, nid, **ids):
    n = store.get(nid)
    n.update(ids)
    store.save(n)


def test_adding_it_again_finds_it_and_merge_keeps_one_paper(client, tmp_path):
    from app import server
    store = server.store
    pre = client.post("/api/import/pdf", files={"file": ("pre.pdf", make_pdf(PRE, "Electron drift instability."), "application/pdf")}).json()
    pre = pre["id"]
    client.put(f"/api/notes/{pre}", json={"title": PRE, "authors": AUTHORS, "year": 2019})
    _ids(store, pre, arxiv="1907.08407")
    client.post("/api/notes", json={"type": "fragment", "parent": pre, "quote": "Electron drift instability", "body": "mine"})
    topic = client.post("/api/topics", json={"name": "Hall thrusters", "members": [pre]}).json()["slug"]

    # the journal version as a PDF: added (it's a file of its own), and flagged as the same paper
    pub = client.post("/api/import/pdf", files={"file": ("pub.pdf", make_pdf(PUB + " v2", "Published version."), "application/pdf")}).json()
    pub = pub["id"]
    client.put(f"/api/notes/{pub}", json={"title": PUB, "authors": ["D. Mandal", "Y. Elskens", "N. Lemoine", "F. Doveil"],
                                          "year": 2020})
    _ids(store, pub, doi="10.1063/1.5134148", arxiv="1911.03556")  # neither matches the preprint's
    assert [o["id"] for o in client.get(f"/api/notes/{pre}/duplicates").json()["items"]] == [pub]
    row = client.get(f"/api/notes/{pub}/duplicates").json()["items"][0]
    assert row["id"] == pre and row["has_pdf"] and row["year"] == 2019

    # Not the same: never offered again, either way round
    assert client.post(f"/api/notes/{pub}/not-duplicate", json={"other": pre}).json()["ok"]
    assert client.get(f"/api/notes/{pre}/duplicates").json()["items"] == []
    (store.root / "state" / "not_duplicates.json").unlink()
    assert len(client.get(f"/api/notes/{pre}/duplicates").json()["items"]) == 1

    # merge into the journal version: it keeps its PDF, the preprint's is put aside; highlight and region come along
    r = client.post(f"/api/notes/{pub}/merge-duplicate", json={"from": pre})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["merged"]["set_aside"]["pdf"] == "kept" and Path(out["merged"]["set_aside"]["path"]).is_file()
    assert not store.exists(pre) and store.pdf_path(pub).is_file()
    assert any(k["parent"] == pub and k["quote"] == "Electron drift instability" for k in store.children(pub))
    assert [t["slug"] for t in client.get(f"/api/notes/{pub}/regions").json()["topics"]] == [topic]
    assert client.get(f"/api/notes/{pub}").json()["doi"] == "10.1063/1.5134148"

    # adding the preprint again by its details gives the paper you have, not a second one
    from app import server as srv
    n = srv._import_meta({"title": PRE, "authors": AUTHORS, "year": 2019, "arxiv": "1907.08407x"})
    assert n["id"] == pub and n["duplicate"] is True
