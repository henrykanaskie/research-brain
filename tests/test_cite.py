"""Citing: CSL records, citation keys, formatting (citeproc-py + vendored CSL styles), BibTeX/BibLaTeX/RIS/
CSL-JSON exports, auto-updating .bib files and user style files. No network.

Run:  python3 -m pytest -q tests/test_cite.py
"""
from __future__ import annotations

import importlib
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import bibexport, cite  # noqa: E402

# ---------------------------------------------------------------------------------------------- items
JOURNAL = {  # many authors, accents, a particle, a suffix, an acronym and a proper noun in the title
    "id": "j", "type": "article-journal",
    "title": "Electron cross-field transport in E×B Hall thruster discharges: DNA of the breathing mode",
    "author": [{"family": "Bœuf", "given": "Jean-Pierre"}, {"family": "Garrigues", "given": "Laurent"},
               {"family": "Beethoven", "given": "Ludwig", "non-dropping-particle": "van"},
               {"family": "Müller", "given": "Jörg"}, {"family": "King", "given": "Martin", "suffix": "Jr."},
               {"family": "Smith", "given": "Ann B."}, {"family": "Lee", "given": "Dae-Hyun"},
               {"family": "Øster", "given": "Åsa"}],
    "container-title": "Physics of Plasmas", "container-title-short": "Phys. Plasmas",
    "volume": "25", "issue": "6", "page": "061204", "issued": {"date-parts": [[2018, 6, 3]]},
    "DOI": "10.1063/1.5017033", "publisher": "AIP Publishing",
}
PREPRINT = {
    "id": "p", "type": "article", "title": "Neural operators for plasma turbulence",
    "author": [{"family": "Doe", "given": "Jane"}, {"family": "Roe", "given": "Richard"}],
    "issued": {"date-parts": [[2024, 1, 5]]}, "number": "2401.01234", "archive": "arXiv",
    "URL": "https://arxiv.org/abs/2401.01234",
}
CONFERENCE = {
    "id": "c", "type": "paper-conference", "title": "Ion thruster grid erosion modelling with machine learning",
    "author": [{"family": "Wirz", "given": "Richard E."}, {"family": "Kanaskie", "given": "Henry"}],
    "container-title": "AIAA Propulsion and Energy 2021 Forum", "publisher": "American Institute of Aeronautics and Astronautics",
    "publisher-place": "Virtual Event", "page": "3400", "issued": {"date-parts": [[2021, 8, 9]]},
    "DOI": "10.2514/6.2021-3400",
}
CHAPTER = {
    "id": "b", "type": "chapter", "title": "Langmuir probes",
    "author": [{"family": "Hutchinson", "given": "Ian H."}], "editor": [{"family": "Chen", "given": "Francis F."}],
    "container-title": "Principles of Plasma Diagnostics", "publisher": "Cambridge University Press",
    "publisher-place": "Cambridge", "page": "55-104", "issued": {"date-parts": [[2002]]}, "edition": "2",
}
ITEMS = [JOURNAL, PREPRINT, CONFERENCE, CHAPTER]


def note(nid, **kw):
    base = {"id": nid, "type": "paper", "title": "", "authors": [], "year": None, "venue": "", "doi": "",
            "arxiv": "", "url": "", "abstract": "", "created": "", "_extra": {}}
    base.update(kw)
    return base


# ---------------------------------------------------------------------------------------------- csl_for
def test_csl_for_legacy_fields():
    n = note("hall-2018", title="Kinetic simulations of Hall thrusters", authors=["Jean-Pierre Boeuf", "Garrigues, Laurent",
             "Ludwig van Beethoven", "ATLAS Collaboration", "Martin Luther King Jr."], year=2018,
             venue="Physics of Plasmas", doi="10.1063/1.5017033")
    it = cite.csl_for(n)
    assert it["id"] == "hall-2018" and it["type"] == "article-journal"
    assert it["author"][0] == {"family": "Boeuf", "given": "Jean-Pierre"}
    assert it["author"][1] == {"family": "Garrigues", "given": "Laurent"}
    assert it["author"][2] == {"family": "Beethoven", "given": "Ludwig", "non-dropping-particle": "van"}
    assert it["author"][3] == {"literal": "ATLAS Collaboration"}
    assert it["author"][4] == {"family": "King", "given": "Martin Luther", "suffix": "Jr."}
    assert it["issued"] == {"date-parts": [[2018]]}
    assert it["container-title"] == "Physics of Plasmas" and it["DOI"] == "10.1063/1.5017033"

    ax = cite.csl_for(note("nn", title="Neural operators", authors=["Jane Doe"], year=2024, arxiv="2401.01234"))
    assert ax["type"] == "article" and ax["number"] == "2401.01234" and ax["archive"] == "arXiv"
    assert ax["URL"] == "https://arxiv.org/abs/2401.01234" and cite.arxiv_id(ax) == "2401.01234"
    assert cite.csl_for(note("c", title="X", venue="Proceedings of the IEEE Conference on Plasma Science"))["type"] == "paper-conference"
    assert cite.csl_for(note("t", title="X", venue="PhD thesis, Oregon State University"))["type"] == "thesis"
    assert cite.csl_for(note("w", type="note", title="My idea"))["type"] in ("article", "webpage")


def test_csl_for_prefers_csl_property_and_normalises_it():
    raw = {"type": "journal-article", "title": "From a resolver", "author": ["Jane Doe", {"family": "Roe", "given": "R."}],
           "issued": "2020-05-02", "volume": 7, "page": "1-9", "junk": None}
    n = note("x", title="Legacy title", authors=["Somebody Else"], year=1999, csl=raw)
    it = cite.csl_for(n)
    assert it["type"] == "article-journal" and it["title"] == "From a resolver" and it["id"] == "x"
    assert it["author"] == [{"family": "Doe", "given": "Jane"}, {"family": "Roe", "given": "R."}]
    assert it["issued"] == {"date-parts": [[2020, 5, 2]]} and it["volume"] == "7" and "junk" not in it
    # an older vault keeps it in the extra properties
    n2 = note("y", title="T", _extra={"csl": {"type": "book", "title": "Book"}})
    assert cite.csl_for(n2)["type"] == "book"


# ---------------------------------------------------------------------------------------------- keys
def test_citekeys_generation_and_disambiguation():
    notes = [note("a", title="The kinetic theory of gases", authors=["Jean-Pierre Bœuf"], year=2018, created="2020-01-01"),
             note("b", title="Kinetic theory, again", authors=["J.-P. Boeuf"], year=2018, created="2021-01-01"),
             note("c", title="A kinetic model", authors=["Boeuf, J."], year=2018, created="2019-01-01"),
             note("d", title="On the 2D Ψ-function", authors=["Ludwig van Beethoven"], year=None, created="2019"),
             note("e", title="", authors=[], year=None, created="2019"),
             note("f", type="fragment", title="frag", created="2019")]
    keys = cite.assign_keys(notes)
    assert "f" not in keys  # fragments are cited through their paper
    # oldest first: c keeps the plain key, then a, then b
    assert keys["c"] == ("boeuf2018kinetic", False)
    assert keys["a"] == ("boeuf2018kinetica", False)
    assert keys["b"] == ("boeuf2018kineticb", False)
    assert keys["d"][0] == "vanbeethoven2d"
    assert keys["e"][0] == "ref"
    # deterministic: the same library gives the same keys in any order
    assert cite.assign_keys(list(reversed(notes))) == keys
    # a persisted key wins, even over an older note's generated key
    notes[1]["_extra"]["citekey"] = "boeuf2018kinetic"
    keys2 = cite.assign_keys(notes)
    assert keys2["b"] == ("boeuf2018kinetic", True)
    assert keys2["c"][0] == "boeuf2018kinetica"
    # a stored key with spaces is cleaned, never used raw
    notes[4]["_extra"]["citekey"] = "my key{}"
    assert cite.assign_keys(notes)["e"][0] == "mykey"


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("RB_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("RB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RB_FAKE_CLAUDE", "1")
    monkeypatch.setenv("RB_EMBEDDER", "tfidf")
    monkeypatch.setenv("RB_AUTO_EXPORT", "0")  # tests drive the exporter directly
    for m in [m for m in sys.modules if m.startswith("app")]:
        del sys.modules[m]
    srv = importlib.import_module("app.server")
    from fastapi.testclient import TestClient
    return TestClient(srv.app), srv


def add(srv, **kw):
    kw.setdefault("type", "paper")
    return srv.store.create(kw)


def test_keys_are_persisted_on_first_export_and_never_change(server):
    client, srv = server
    a = add(srv, title="Kinetic simulations of Hall thrusters", authors=["Jean-Pierre Boeuf"], year=2018)
    before = srv.store.get(a["id"])["updated"]
    r = client.post("/api/cite/keys", json={"ids": [a["id"]]}).json()
    assert r["latex"] == "\\cite{boeuf2018kinetic}" and r["keys"] == {a["id"]: "boeuf2018kinetic"}
    n = srv.store.get(a["id"])
    assert cite.stored_key(n) == "boeuf2018kinetic"
    assert n["updated"] == before  # bookkeeping doesn't count as an edit
    assert "citekey: boeuf2018kinetic" in srv.store.note_path(a["id"]).read_text()
    # an older paper with the same base key arrives later: the exported key doesn't move
    b = add(srv, title="Kinetic theory", authors=["J. Boeuf"], year=2018)
    srv.store.set_fields(b["id"], created="2000-01-01T00:00:00")
    bib = client.get(f"/api/export/bibtex?ids={a['id']},{b['id']}").text
    assert "@misc{boeuf2018kinetic," in bib and "@misc{boeuf2018kinetica," in bib
    assert cite.stored_key(srv.store.get(b["id"])) == "boeuf2018kinetica"
    # the title changes: the key stays
    srv.store.update(a["id"], {"title": "Totally different"})
    assert client.post("/api/cite/keys", json={"ids": [a["id"]]}).json()["latex"] == "\\cite{boeuf2018kinetic}"
    # set by hand; a key another paper owns is refused
    assert client.put(f"/api/cite/keys/{a['id']}", json={"citekey": "boeuf:hall"}).json()["citekey"] == "boeuf:hall"
    assert client.put(f"/api/cite/keys/{b['id']}", json={"citekey": "boeuf:hall"}).status_code == 409
    assert client.put(f"/api/cite/keys/{b['id']}", json={"citekey": "has space"}).status_code == 400


def test_zotero_citation_keys_are_kept(tmp_path, monkeypatch):
    from test_zotero import _zedit, build_zotero, run_import
    zdir = build_zotero(tmp_path / "Zotero")
    # a pinned Better BibTeX key in Extra, and BBT's own database for another item
    _zedit(zdir, "INSERT INTO itemDataValues (value) VALUES (?)", "Citation Key: raissi2019physics\nfoo: bar")
    vid = sqlite3.connect(zdir / "zotero.sqlite").execute("SELECT max(valueID) FROM itemDataValues").fetchone()[0]
    iid = sqlite3.connect(zdir / "zotero.sqlite").execute("SELECT itemID FROM items WHERE key='PINN2019'").fetchone()[0]
    _zedit(zdir, "INSERT OR REPLACE INTO itemData (itemID, fieldID, valueID) VALUES (?, 16, ?)", iid, vid)
    with sqlite3.connect(zdir / "better-bibtex.sqlite") as db:
        db.execute("CREATE TABLE citationkey (itemID INT, itemKey TEXT, libraryID INT, citationKey TEXT, pinned INT)")
        db.execute("INSERT INTO citationkey VALUES (1, 'HALL2016', 1, 'physicist2016hall', 0)")
    monkeypatch.setenv("RB_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("RB_FAKE_CLAUDE", "1")
    monkeypatch.setenv("RB_EMBEDDER", "tfidf")
    monkeypatch.setenv("RB_ZOTERO_DIR", str(zdir))
    monkeypatch.setenv("RB_AUTO_EXPORT", "0")
    for m in [m for m in sys.modules if m.startswith("app")]:
        del sys.modules[m]
    srv = importlib.import_module("app.server")
    for name in ("lookup", "s2_paper", "discover"):
        monkeypatch.setattr(srv.scholar, name, lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network")))
    from fastapi.testclient import TestClient
    client = TestClient(srv.app)
    job = run_import(client)
    assert job["state"] == "done", job
    by_z = {n["zotero_key"]: n for n in srv.store.all() if n["type"] == "paper"}
    assert cite.stored_key(by_z["PINN2019"]) == "raissi2019physics"
    assert cite.stored_key(by_z["HALL2016"]) == "physicist2016hall"
    keys = client.post("/api/cite/keys", json={"ids": [by_z["PINN2019"]["id"], by_z["HALL2016"]["id"]]}).json()
    assert keys["latex"] == "\\cite{raissi2019physics,physicist2016hall}"
    # a re-import never replaces a key (the user may have changed it)
    client.put(f"/api/cite/keys/{by_z['PINN2019']['id']}", json={"citekey": "mine2019"})
    run_import(client)
    assert cite.stored_key(srv.store.get(by_z["PINN2019"]["id"])) == "mine2019"


# ---------------------------------------------------------------------------------------------- formatting
def fmt(style, mode="bibliography", items=ITEMS):
    return cite.format_items([dict(i) for i in items], style, mode)


def entry(style, iid):
    out = fmt(style)
    return next(e for e in out["entries"] if e["id"] == iid)


def test_styles_vendored_with_licence():
    ids = {s["id"]: s for s in cite.list_styles()}
    for sid in ("american-physics-society", "american-institute-of-physics", "ieee",
                "american-institute-of-aeronautics-and-astronautics", "nature", "apa", "chicago-author-date"):
        assert sid in ids and not ids[sid]["error"], sid
        assert "creativecommons.org/licenses/by-sa/3.0" in (cite.STYLES_DIR / f"{sid}.csl").read_text()
    assert (cite.LOCALES_DIR / "locales-en-US.xml").exists()
    assert "CC BY-SA 3.0" in (cite.CSL_DIR / "LICENSE.md").read_text()


def test_aps_output():
    j = entry("american-physics-society", "j")["text"]
    # APS: initials, "and" before the last author, abbreviated journal, bold volume (html), page, (year)
    assert j.startswith("[1] J.-P. Bœuf, L. Garrigues, L. van Beethoven, J. Müller, M. King Jr., A. B. Smith, D.-H. Lee, and Å. Øster,")
    assert j.endswith("Phys. Plasmas 25, 061204 (2018).")
    assert "<b>25</b>" in entry("american-physics-society", "j")["html"]
    p = entry("american-physics-society", "p")["text"]
    assert p.startswith("[2] J. Doe and R. Roe,") and p.endswith("arXiv:2401.01234.") and "ArXiv" not in p
    c = entry("american-physics-society", "c")["text"]
    assert c.startswith("[3] R. E. Wirz and H. Kanaskie,") and "in AIAA Propulsion and Energy 2021 Forum" in c
    assert c.endswith("(American Institute of Aeronautics and Astronautics, Virtual Event, 2021), p. 3400.")
    b = entry("american-physics-society", "b")["text"]
    assert "I. H. Hutchinson" in b and "edited by F. F. Chen" in b and "(Cambridge University Press, Cambridge, 2002)" in b and "pp. 55–104" in b
    assert fmt("american-physics-society", "citation")["text"].strip() in ("[1–4]", "[1-4]", "[1,2,3,4]", "[1, 2, 3, 4]")


def test_aip_ieee_apa_output():
    aip = entry("american-institute-of-physics", "j")["text"]
    assert aip.startswith("1 J.-P. Bœuf") and "Phys. Plasmas 25(6), 061204 (2018)" in aip
    ieee = entry("ieee", "j")["text"]
    assert ieee.startswith("[1] J.-P. Bœuf et al.,") and "vol. 25, no. 6, p. 061204" in ieee and "doi: 10.1063/1.5017033" in ieee
    assert "“Ion thruster grid erosion modelling with machine learning”" in entry("ieee", "c")["text"]
    assert "arXiv" in entry("ieee", "p")["text"]
    assert fmt("ieee", "citation")["text"] == "[1], [2], [3], [4]"
    apa = entry("apa", "j")["text"]
    assert apa.startswith("Bœuf, J.-P., Garrigues, L., van Beethoven, L.,") and "(2018)" in apa
    assert "https://doi.org/10.1063/1.5017033" in apa and "Physics of Plasmas, 25(6), 061204" in apa
    apap = entry("apa", "p")["text"]
    assert apap.startswith("Doe, J., & Roe, R. (2024). Neural operators for plasma turbulence (arXiv:2401.01234). arXiv.")
    assert "https://arxiv.org/abs/2401.01234" in apap
    assert "In F. F. Chen (" in entry("apa", "b")["text"]
    assert fmt("apa", "citation")["text"] == "(Bœuf et al., 2018; Doe & Roe, 2024; Hutchinson, 2002; Wirz & Kanaskie, 2021)"
    # every shipped style formats all four kinds without an error
    for s in cite.list_styles():
        out = fmt(s["id"])
        assert len(out["entries"]) == 4 and all(e["text"] for e in out["entries"]), s["id"]


def test_html_output_is_sanitised():
    evil = dict(JOURNAL, id="e", title='Bad <script>alert(1)</script><img src=x onerror=alert(1)> <i>italic</i>')
    out = fmt("apa", items=[evil])
    assert "<script" not in out["html"] and "<img" not in out["html"] and "onerror" not in out["html"]
    assert "<i>italic</i>" in out["html"]
    assert re.findall(r"<(\w+)", out["html"]) and set(re.findall(r"<(\w+)", out["html"])) <= {"div", "i", "b", "sup", "sub", "span", "em", "strong"}
    assert cite.sanitize_html('<a href="javascript:x">l</a><span style="color:red" onclick="x">s</span><b>b</b>') == "l<span>s</span><b>b</b>"


def test_format_endpoint_and_bad_style(server):
    client, srv = server
    a = add(srv, title="Kinetic simulations", authors=["Jean-Pierre Boeuf"], year=2018, venue="Physics of Plasmas")
    r = client.post("/api/cite/format", json={"ids": [a["id"]], "mode": "citation", "style": "apa"}).json()
    assert r["text"] == "(Boeuf, 2018)" and r["html"] == "(Boeuf, 2018)"
    r = client.post("/api/cite/format", json={"ids": [a["id"]], "format": "text"}).json()
    assert "html" not in r and r["text"].startswith("[1] J.-P. Boeuf, Kinetic simulations")
    assert client.post("/api/cite/format", json={"ids": [a["id"]], "style": "nope"}).status_code == 404
    assert client.post("/api/cite/format", json={"ids": [a["id"]], "mode": "x"}).status_code == 400
    styles = client.get("/api/cite/styles").json()
    assert styles["default"] == "american-physics-society" and styles["copy"] == "formatted"
    assert client.put("/api/cite/settings", json={"style": "apa", "copy": "citekey"}).json() == {"style": "apa", "copy": "citekey"}
    assert client.put("/api/cite/settings", json={"copy": "nope"}).status_code == 400
    assert client.post("/api/cite/format", json={"ids": [a["id"]], "mode": "citation"}).json()["text"] == "(Boeuf, 2018)"


def test_note_and_topic_scopes(server):
    client, srv = server
    a = add(srv, title="Alpha paper", authors=["Ann Alpha"], year=2001)
    b = add(srv, title="Beta paper", authors=["Bob Beta"], year=2002)
    add(srv, title="Gamma paper", authors=["Gus Gamma"], year=2003)
    frag = srv.store.create({"type": "fragment", "parent": b["id"], "title": "a quote", "quote": "q"})
    n = srv.store.create({"type": "note", "title": "Synthesis", "body": f"See [[Alpha paper]] and [[{frag['id']}]]."})
    r = client.post("/api/cite/keys", json={"note": n["id"]}).json()
    assert r["ids"] == [a["id"], b["id"]] and r["latex"] == "\\cite{alpha2001alpha,beta2002beta}"
    bib = client.get(f"/api/export/bibtex?note={n['id']}").text
    assert bib.count("@misc{") == 2 and "gamma" not in bib.lower()
    client.post("/api/topics", json={"name": "Greek", "members": [b["id"], a["id"]]})
    out = client.post("/api/cite/format", json={"topic": "greek", "style": "apa"}).json()
    assert [e["id"] for e in out["entries"]] == [a["id"], b["id"]]
    assert client.get("/api/export/ris?topic=greek").text.count("TY  - ") == 2
    assert client.get("/api/export/bibtex?all=1").text.count("@misc{") == 3
    assert client.get("/api/export/bibtex").status_code == 400
    assert client.get("/api/export/bibtex?topic=nope").status_code == 404
    hits = client.get("/api/cite/search?q=beta").json()
    assert hits[0]["id"] == b["id"] and hits[0]["citekey"] == "beta2002beta" and hits[0]["authors"] == ["Bob Beta"]


# ---------------------------------------------------------------------------------------------- BibTeX / RIS
def test_bibtex_escaping_types_and_fields():
    ents = [("boeuf2018electron", JOURNAL), ("doe2024neural", PREPRINT), ("wirz2021ion", CONFERENCE), ("hutchinson2002langmuir", CHAPTER)]
    bt = bibexport.to_bibtex(ents, "bibtex")
    bl = bibexport.to_bibtex(ents, "biblatex")
    assert "@article{boeuf2018electron," in bt and "@misc{doe2024neural," in bt
    assert "@inproceedings{wirz2021ion," in bt and "@incollection{hutchinson2002langmuir," in bt
    # titles: acronyms, proper nouns (sentence case) braced; the first word isn't
    assert "title = {Electron cross-field transport in {E×B} {Hall} thruster discharges: {DNA} of the breathing mode}" in bl
    assert r"{E$\times$B}" in bt
    # bibtex: ASCII with LaTeX accents; biblatex: UTF-8
    assert r"B{\oe}uf, Jean-Pierre" in bt and r"M{\"u}ller, J{\"o}rg" in bt and r"{\O}ster, {\AA}sa" in bt
    assert "Bœuf, Jean-Pierre" in bl and "Müller, Jörg" in bl
    assert "van Beethoven, Ludwig" in bt and "King, Jr., Martin" in bt
    assert all(ord(ch) < 128 for ch in bt)
    # fields
    assert "journal = {Physics of Plasmas}" in bt and "journaltitle = {Physics of Plasmas}" in bl
    assert "shortjournal = {Phys. Plasmas}" in bl
    assert "year = {2018}" in bt and "month = jun" in bt and "date = {2018-06-03}" in bl
    assert "pages = {55--104}" in bt and "doi = {10.1063/1.5017033}" in bt
    assert "booktitle = {AIAA Propulsion and Energy 2021 Forum}" in bt and "address = {Virtual Event}" in bt
    assert "location = {Virtual Event}" in bl and "editor = {Chen, Francis F.}" in bt
    # arXiv: eprint fields in both, howpublished for plain bibtex styles
    assert "eprint = {2401.01234}" in bt and "archiveprefix = {arXiv}" in bt and "howpublished = {arXiv:2401.01234}" in bt
    assert "eprint = {2401.01234}" in bl and "eprinttype = {arxiv}" in bl
    # thesis and report types
    th = dict(CHAPTER, type="thesis", genre="M.S. thesis", publisher="Oregon State University")
    assert bibexport.to_bibtex([("k", th)]).startswith("@mastersthesis{k,") and "school = {Oregon State University}" in bibexport.to_bibtex([("k", th)])
    assert bibexport.to_bibtex([("k", dict(th, genre="PhD dissertation"))]).startswith("@phdthesis{k,")
    rep = bibexport.to_bibtex([("k", dict(CHAPTER, type="report", number="LA-UR-21-1"))])
    assert rep.startswith("@techreport{k,") and "institution = {Cambridge University Press}" in rep and "number = {LA-UR-21-1}" in rep
    # LaTeX specials are escaped, math passes through, braces always balance
    weird = dict(PREPRINT, title="50% of {ions} & #electrons_cold ~ $T_e^{3/2}$ \\ok}", URL="https://x.org/a%20b#frag")
    w = bibexport.to_bibtex([("w", weird)], "biblatex")
    assert r"50\% of \{ions\} \& \#electrons\_cold \textasciitilde{} $T_e^{3/2}$ \textbackslash{}ok\}" in w
    assert r"url = {https://x.org/a\%20b\#frag}" in w
    depth = 0
    for ch in re.sub(r"\\[{}]", "", w):
        depth += (ch == "{") - (ch == "}")
        assert depth >= 0
    assert depth == 0


def test_bibtex_round_trip_with_bibtexparser():
    bibtexparser = pytest.importorskip("bibtexparser")
    from bibtexparser.bparser import BibTexParser
    from bibtexparser.customization import convert_to_unicode
    ents = [("boeuf2018electron", JOURNAL), ("doe2024neural", PREPRINT), ("wirz2021ion", CONFERENCE), ("hutchinson2002langmuir", CHAPTER)]
    for flavor in ("bibtex", "biblatex"):
        text = bibexport.to_bibtex(ents, flavor, header=bibexport.HEADER)
        parser = BibTexParser(common_strings=True)
        parser.customization = convert_to_unicode
        db = bibtexparser.loads(text, parser=parser)
        got = {e["ID"]: e for e in db.entries}
        assert set(got) == {k for k, _ in ents}, flavor
        j = got["boeuf2018electron"]
        assert j["ENTRYTYPE"] == "article" and j["doi"] == "10.1063/1.5017033" and j["volume"] == "25"
        authors = [a.strip() for a in j["author"].split(" and ")]
        # (bibtexparser 1.x decodes {\\oe} as "øe"; LaTeX itself prints œ)
        assert len(authors) == 8 and authors[0] in ("Bœuf, Jean-Pierre", "Bøeuf, Jean-Pierre") and authors[3] == "Müller, Jörg", (flavor, authors)
        assert authors[7] == "Øster, Åsa" and authors[4] == "King, Jr., Martin", (flavor, authors)
        title = re.sub(r"[{}]", "", j["title"])
        assert title.replace("$\\times$", "×") == JOURNAL["title"], (flavor, title)
        assert got["hutchinson2002langmuir"]["pages"] == "55--104"
        assert got["doe2024neural"]["eprint"] == "2401.01234"
        assert got["wirz2021ion"]["ENTRYTYPE"] == "inproceedings"
        assert re.sub(r"[{}]", "", got["wirz2021ion"]["booktitle"]) == CONFERENCE["container-title"]


def test_ris_and_csl_json():
    ents = [("boeuf2018electron", JOURNAL), ("doe2024neural", PREPRINT), ("wirz2021ion", CONFERENCE), ("hutchinson2002langmuir", CHAPTER)]
    ris = bibexport.to_ris(ents)
    recs = [r for r in ris.split("\r\n\r\n") if r.strip()]
    assert len(recs) == 4
    j = recs[0].split("\r\n")
    assert j[0] == "TY  - JOUR" and j[-1].startswith("ER  -")
    assert "AU  - Bœuf, Jean-Pierre" in j and "AU  - King, Martin, Jr." in j and "AU  - van Beethoven, Ludwig" in j
    assert "PY  - 2018" in j and "DA  - 2018/06/03/" in j and "SP  - 061204" in j and "DO  - 10.1063/1.5017033" in j
    assert "T2  - Physics of Plasmas" in j and "J2  - Phys. Plasmas" in j
    p = recs[1].split("\r\n")
    assert "TY  - UNPB" in p and "AN  - arXiv:2401.01234" in p
    c = recs[2].split("\r\n")
    assert "TY  - CPAPER" in c
    b = recs[3].split("\r\n")
    assert "TY  - CHAP" in b and "ED  - Chen, Francis F." in b and "SP  - 55" in b and "EP  - 104" in b
    data = json.loads(bibexport.to_csl_json(ents))
    assert [d["id"] for d in data] == [k for k, _ in ents] and data[0]["author"][0]["family"] == "Bœuf"


# ---------------------------------------------------------------------------------------------- user styles
STYLE_OK = (cite.STYLES_DIR / "american-physics-society.csl").read_text().replace(
    "<title>American Physical Society</title>", "<title>My lab style</title>")


def test_user_style_upload_validation(server, tmp_path):
    client, srv = server
    r = client.post("/api/cite/styles", json={"filename": "my lab.csl", "content": STYLE_OK})
    assert r.status_code == 201 and r.json()["id"] == "user:my-lab" and r.json()["title"] == "My lab style"
    assert (srv.VAULT / "styles" / "my-lab.csl").exists()
    a = add(srv, title="Kinetic", authors=["Jean Boeuf"], year=2018)
    assert client.post("/api/cite/format", json={"ids": [a["id"]], "style": "user:my-lab"}).json()["text"].startswith("[1] J. Boeuf")
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET")
    xxe = ('<?xml version="1.0"?><!DOCTYPE style [<!ENTITY x SYSTEM "file://' + str(secret) + '">]>'
           '<style xmlns="http://purl.org/net/xbiblio/csl" version="1.0"><info><title>&x;</title></info></style>')
    r = client.post("/api/cite/styles", json={"filename": "xxe.csl", "content": xxe})
    assert r.status_code == 400 and "DOCTYPE" in r.json()["detail"]
    lol = '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;">]><style>&lol2;</style>'
    assert client.post("/api/cite/styles", json={"filename": "lol.csl", "content": lol}).status_code == 400
    assert client.post("/api/cite/styles", json={"filename": "x.csl", "content": "<not-closed"}).status_code == 400
    assert client.post("/api/cite/styles", json={"filename": "x.csl", "content": "<html><body/></html>"}).status_code == 400
    dep = ('<style xmlns="http://purl.org/net/xbiblio/csl" version="1.0"><info><title>Dep</title>'
           '<link href="http://www.zotero.org/styles/apa" rel="independent-parent"/></info></style>')
    r = client.post("/api/cite/styles", json={"filename": "dep.csl", "content": dep})
    assert r.status_code == 400 and "dependent" in r.json()["detail"]
    assert client.post("/api/cite/styles", json={"filename": "big.csl", "content": "<a>" + "x" * (1 << 20) + "</a>"}).status_code == 400
    assert not (srv.VAULT / "styles" / "xxe.csl").exists()
    # a broken file dropped into the folder by hand is listed with an error, never crashes the list
    (srv.VAULT / "styles" / "broken.csl").write_text("<style")
    lst = {s["id"]: s for s in client.get("/api/cite/styles").json()["styles"]}
    assert lst["user:broken"]["error"] and not lst["user:my-lab"]["error"]
    assert client.post("/api/cite/format", json={"ids": [a["id"]], "style": "user:broken"}).status_code == 422
    assert client.delete("/api/cite/styles/user:my-lab").json() == {"ok": True}
    assert client.delete("/api/cite/styles/apa").status_code == 404


# ---------------------------------------------------------------------------------------------- auto-export
def test_auto_export_path_validation(server, tmp_path):
    client, srv = server
    from app import cite_api
    good = tmp_path / "thesis" / "refs.bib"
    good.parent.mkdir()
    assert cite_api.validate_path(str(good), srv.VAULT) == good.resolve()
    bad = {
        "refs.bib": "absolute",
        str(tmp_path / "refs.txt"): ".bib",
        str(tmp_path / "missing" / "refs.bib"): "doesn't exist",
        str(tmp_path / "thesis" / ".." / "x.bib"): "..",
        str(ROOT / "static" / "refs.bib"): "program folder",
        str(srv.VAULT / "notes" / "refs.bib"): "internal",
    }
    for p, why in bad.items():
        with pytest.raises(cite_api.PathError, match=re.escape(why) if why != "absolute" else "absolute"):
            cite_api.validate_path(p, srv.VAULT)
    mine = tmp_path / "thesis" / "handwritten.bib"
    mine.write_text("@article{x, title={Mine}}\n")
    with pytest.raises(cite_api.PathError, match="wasn't written by research brain"):
        cite_api.validate_path(str(mine), srv.VAULT)
    link = tmp_path / "thesis" / "link.bib"
    os.symlink(tmp_path / "elsewhere.bib", link)
    with pytest.raises(cite_api.PathError, match="symbolic link"):
        cite_api.validate_path(str(link), srv.VAULT)
    assert client.post("/api/cite/auto-exports", json={"path": "relative.bib"}).status_code == 400
    assert client.post("/api/cite/auto-exports", json={"path": str(good), "scope": "topic:nope"}).status_code == 404
    assert client.post("/api/cite/auto-exports", json={"path": str(good), "flavor": "x"}).status_code == 400
    assert not (tmp_path / "elsewhere.bib").exists() and mine.read_text().startswith("@article{x")


def test_auto_export_rewrites_on_change(server, tmp_path):
    client, srv = server
    from app import cite_api
    a = add(srv, title="Alpha paper", authors=["Ann Alpha"], year=2001)
    target = tmp_path / "out" / "library.bib"
    target.parent.mkdir()
    r = client.post("/api/cite/auto-exports", json={"path": str(target), "flavor": "bibtex"})
    assert r.status_code == 201, r.text
    e = r.json()
    assert e["written"] and not e["error"] and e["entries"] == 1
    text = target.read_text()
    assert text.startswith(bibexport.HEADER) and "@misc{alpha2001alpha," in text
    assert os.listdir(target.parent) == ["library.bib"]  # no temp files left behind
    assert client.post("/api/cite/auto-exports", json={"path": str(target)}).status_code == 409
    lst = client.get("/api/cite/auto-exports").json()["exports"]
    assert len(lst) == 1 and lst[0]["path"] == str(target.resolve()) and lst[0]["written"]

    exp = cite_api.ctx.exporter
    mtime = target.stat().st_mtime_ns
    exp.run_all()  # nothing changed: the file isn't rewritten
    assert target.stat().st_mtime_ns == mtime
    srv.store.update(a["id"], {"title": "Alpha paper, revised", "venue": "Physics of Plasmas"})
    b = add(srv, title="Beta paper", authors=["Bob Beta"], year=2002)
    exp.run_all()
    text = target.read_text()
    assert "@article{alpha2001alpha," in text and "Alpha paper, revised" in text and "@misc{beta2002beta," in text
    srv.store.delete(b["id"])
    exp.run_all()
    assert "beta2002beta" not in target.read_text()

    # the background thread picks changes up by itself
    exp.INTERVAL = 0.1
    exp.start()
    try:
        add(srv, title="Gamma paper", authors=["Gus Gamma"], year=2003)
        for _ in range(100):
            if "gamma2003gamma" in target.read_text():
                break
            time.sleep(0.05)
        assert "gamma2003gamma" in target.read_text()
    finally:
        exp.stop()

    # a topic auto-export follows the topic's membership
    tt = tmp_path / "out" / "topic.bib"
    client.post("/api/topics", json={"name": "Greek", "members": [a["id"]]})
    assert client.post("/api/cite/auto-exports", json={"path": str(tt), "scope": "topic:greek", "flavor": "biblatex"}).status_code == 201
    assert "alpha2001alpha" in tt.read_text() and "gamma" not in tt.read_text()
    # the file was replaced by a symlink behind our back: refuse, don't follow
    os.unlink(tt)
    os.symlink(tmp_path / "victim.bib", tt)
    res = exp.run_all()
    assert any("symbolic link" in x["error"] for x in res) and not (tmp_path / "victim.bib").exists()
    eid = next(x["id"] for x in client.get("/api/cite/auto-exports").json()["exports"] if x["scope"] == "topic:greek")
    assert client.delete(f"/api/cite/auto-exports/{eid}").json()["ok"]
    assert len(client.get("/api/cite/auto-exports").json()["exports"]) == 1
