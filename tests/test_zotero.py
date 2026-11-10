"""Zotero import tests against a fake Zotero 7 data directory (real table layout, made-up library).

Run:  .venv/bin/python -m pytest -q tests/test_zotero.py
Build a fake library to point the app at:  python tests/test_zotero.py /some/dir   (then RB_ZOTERO_DIR=/some/dir)
"""
import hashlib
import importlib
import io
import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------- fake Zotero 7 library
# Table definitions follow Zotero's resource/schema/userdata.sql (only the tables we read, plus a few neighbours).
SCHEMA = """
CREATE TABLE version (schema TEXT PRIMARY KEY, version INT NOT NULL);
CREATE TABLE libraries (libraryID INTEGER PRIMARY KEY, type TEXT NOT NULL, editable INT NOT NULL,
    filesEditable INT NOT NULL, version INT NOT NULL DEFAULT 0, storageVersion INT NOT NULL DEFAULT 0,
    lastSync INT NOT NULL DEFAULT 0, archived INT NOT NULL DEFAULT 0);
CREATE TABLE itemTypes (itemTypeID INTEGER PRIMARY KEY, typeName TEXT, templateItemTypeID INT, display INT DEFAULT 1);
CREATE TABLE itemTypesCombined (itemTypeID INT NOT NULL, typeName TEXT NOT NULL, display INT DEFAULT 1 NOT NULL,
    custom INT NOT NULL, PRIMARY KEY (itemTypeID));
CREATE TABLE fields (fieldID INTEGER PRIMARY KEY, fieldName TEXT, fieldFormatID INT);
CREATE TABLE fieldsCombined (fieldID INT NOT NULL, fieldName TEXT NOT NULL, label TEXT, fieldFormatID INT,
    custom INT NOT NULL, PRIMARY KEY (fieldID));
CREATE TABLE creatorTypes (creatorTypeID INTEGER PRIMARY KEY, creatorType TEXT);
CREATE TABLE items (itemID INTEGER PRIMARY KEY, itemTypeID INT NOT NULL,
    dateAdded TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, dateModified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    clientDateModified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, libraryID INT NOT NULL, key TEXT NOT NULL,
    version INT NOT NULL DEFAULT 0, synced INT NOT NULL DEFAULT 0, UNIQUE (libraryID, key));
CREATE TABLE itemDataValues (valueID INTEGER PRIMARY KEY, value UNIQUE);
CREATE TABLE itemData (itemID INT, fieldID INT, valueID, PRIMARY KEY (itemID, fieldID));
CREATE TABLE itemNotes (itemID INTEGER PRIMARY KEY, parentItemID INT, note TEXT, title TEXT);
CREATE TABLE itemAttachments (itemID INTEGER PRIMARY KEY, parentItemID INT, linkMode INT, contentType TEXT,
    charsetID INT, path TEXT, syncState INT DEFAULT 0, storageModTime INT, storageHash TEXT,
    lastProcessedModificationTime INT, lastRead INT);
CREATE TABLE itemAnnotations (itemID INTEGER PRIMARY KEY, parentItemID INT NOT NULL, type INTEGER NOT NULL,
    authorName TEXT, text TEXT, comment TEXT, color TEXT, pageLabel TEXT, sortIndex TEXT NOT NULL,
    position TEXT NOT NULL, isExternal INT NOT NULL);
CREATE TABLE tags (tagID INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE itemTags (itemID INT NOT NULL, tagID INT NOT NULL, type INT NOT NULL, PRIMARY KEY (itemID, tagID));
CREATE TABLE creators (creatorID INTEGER PRIMARY KEY, firstName TEXT, lastName TEXT, fieldMode INT,
    UNIQUE (lastName, firstName, fieldMode));
CREATE TABLE itemCreators (itemID INT NOT NULL, creatorID INT NOT NULL, creatorTypeID INT NOT NULL DEFAULT 1,
    orderIndex INT NOT NULL DEFAULT 0, PRIMARY KEY (itemID, creatorID, creatorTypeID, orderIndex),
    UNIQUE (itemID, orderIndex));
CREATE TABLE collections (collectionID INTEGER PRIMARY KEY, collectionName TEXT NOT NULL,
    parentCollectionID INT DEFAULT NULL, clientDateModified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    libraryID INT NOT NULL, key TEXT NOT NULL, version INT NOT NULL DEFAULT 0, synced INT NOT NULL DEFAULT 0,
    UNIQUE (libraryID, key));
CREATE TABLE collectionItems (collectionID INT NOT NULL, itemID INT NOT NULL, orderIndex INT NOT NULL DEFAULT 0,
    PRIMARY KEY (collectionID, itemID));
CREATE TABLE deletedItems (itemID INTEGER PRIMARY KEY, dateDeleted DEFAULT CURRENT_TIMESTAMP NOT NULL);
CREATE TABLE deletedCollections (collectionID INTEGER PRIMARY KEY, dateDeleted DEFAULT CURRENT_TIMESTAMP NOT NULL);
CREATE TABLE groups (groupID INTEGER PRIMARY KEY, libraryID INT NOT NULL UNIQUE, name TEXT NOT NULL,
    description TEXT NOT NULL, version INT NOT NULL);
"""
# Zotero 7 global schema ids (subset)
ITEM_TYPES = {1: "annotation", 2: "artwork", 3: "attachment", 7: "bookSection", 11: "conferencePaper",
              22: "journalArticle", 28: "note", 30: "preprint", 36: "thesis"}
FIELDS = {1: "title", 2: "abstractNote", 6: "date", 13: "url", 16: "extra", 26: "publicationTitle",
          27: "volume", 58: "DOI", 69: "proceedingsTitle", 110: "archiveID", 111: "repository",
          115: "university", 116: "bookTitle"}
CREATOR_TYPES = {1: "artist", 8: "author", 10: "editor", 11: "seriesEditor"}


def make_pdf(title: str, pages: list[str]) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setTitle(title)
    for i, body in enumerate(pages):
        if i == 0:
            c.setFont("Helvetica-Bold", 15)
            c.drawString(72, 720, title)
        c.setFont("Helvetica", 10)
        y = 690
        for line in body.split("\n"):
            c.drawString(72, y, line)
            y -= 14
        c.showPage()
    c.save()
    return buf.getvalue()


LIBRARY = [
    dict(key="ATTN2017", type="conferencePaper", title="Attention Is All You Need", date="2017-06-12",
         authors=[("Ashish", "Vaswani"), ("Noam", "Shazeer"), ("Niki", "Parmar")],
         editors=[("Isabelle", "Guyon")], proceedingsTitle="Advances in Neural Information Processing Systems",
         url="https://arxiv.org/abs/1706.03762v7", tags=["transformers", "attention"],
         abstract="We propose the Transformer, based solely on attention mechanisms.",
         collections=["ML"],
         pdf=("ATTNPDF1", "Vaswani et al. - 2017 - Attention Is All You Need.pdf",
              ["The Transformer uses multi-head self attention.", "Scaled dot-product attention.",
               "Attention(Q,K,V) = softmax(QK^T/sqrt(d_k))V", "Results on WMT 2014."]),
         notes=["<div data-schema-version=\"9\"><h1>Reading notes</h1><p>Replaces <strong>recurrence</strong> with "
                "attention.</p><ul><li>Multi-head</li><li>Positional encodings: <em>sinusoidal</em></li></ul>"
                "<p>See <a href=\"https://jalammar.github.io/illustrated-transformer/\">the illustrated version</a>.</p></div>"],
         annotations=[dict(key="ANNA0001", type=1, text="We propose a new simple network architecture, the Transformer,",
                           comment="The core claim.", pageLabel="1", pageIndex=0),
                      dict(key="ANNA0002", type=1, text="Attention(Q, K, V) = softmax(QK^T / √d_k) V",
                           comment="Scaling keeps softmax\nout of saturation.", pageLabel="4", pageIndex=3),
                      dict(key="ANNA0003", type=2, text=None, comment="Compare with linear attention later.",
                           pageLabel="5", pageIndex=4),
                      dict(key="ANNA0004", type=4, text=None, comment="", pageLabel="5", pageIndex=4)]),
    dict(key="HALL2016", type="journalArticle", title="Hall thruster plasma oscillations: the breathing mode",
         date="March 2016", authors=[("A.", "Physicist"), ("B.", "Experimenter")],
         publicationTitle="Physics of Plasmas", DOI="10.1063/1.4945558", tags=["plasma", "Hall thruster"],
         abstract="Breathing mode oscillations in Hall effect thrusters.", collections=["Plasma", "Hall thrusters"],
         pdf=("HALLPDF1", "breathing.pdf", ["Breathing mode near 20 kHz.", "Ionization front moves."]),
         annotations=[dict(key="ANNH0001", type=1, text="oscillations in the 10-30 kHz range",
                           comment="", pageLabel="ii", pageIndex=1)]),
    dict(key="PINN2019", type="preprint", title="Physics-informed neural networks for plasma transport",
         date="2019", authors=[("Maziar", "Raissi"), ("", "PESPL Consortium")], repository="arXiv",
         archiveID="arXiv:1901.01234", DOI="10.48550/arXiv.1901.01234", tags=["pinn", "plasma"],
         collections=["Plasma"], notes=["<p>Child note one.</p>", "<ol><li>first</li><li>second</li></ol>"]),
    dict(key="CRM2021", type="thesis", title="A collisional-radiative model for krypton", date="2021-05",
         authors=[("Some", "Student")], university="Oregon State University",
         extra="arXiv: 2105.00001\ntype: MS thesis", collections=["Kr CRM"]),
    dict(key="TRASH001", type="journalArticle", title="Deleted paper that should not import", date="2020",
         authors=[("No", "Body")], deleted=True, collections=["ML"]),
    dict(key="BOOKSEC1", type="bookSection", title="Plasma diagnostics primer", date="2010",
         authors=[("Ivan", "Hutchinson")], bookTitle="Principles of Plasma Diagnostics", collections=[],
         pdf=("MISSINGP", "gone.pdf", None)),  # attachment row but the file is missing on disk
]
COLLECTIONS = [("COLML001", "ML", None), ("COLPLAS1", "Plasma", None), ("COLHALL1", "Hall thrusters", "COLPLAS1"),
               ("COLKRCRM", "Kr CRM", "COLPLAS1"), ("COLEMPTY", "Empty", None)]


# A group library (libraryID 2): one paper with a highlight, one collection. Only built with groups=True.
GROUP_LIBRARY = [
    dict(key="GRPLASMA", type="journalArticle", title="Group paper on plasma diagnostics", date="2022",
         authors=[("Lab", "Member")], DOI="10.9999/group.2022", tags=["group"], collections=["Lab reading"],
         pdf=("GRPPDF01", "group.pdf", ["Langmuir probes measure plasma density."]),
         annotations=[dict(key="ANNG0001", type=1, text="Langmuir probes measure plasma density",
                           comment="Group highlight.", pageLabel="1", pageIndex=0)]),
]
GROUP_COLLECTIONS = [("COLGRP01", "Lab reading", None)]


def build_zotero(zdir: Path, wal: bool = False, groups: bool = False) -> Path:
    """Create a Zotero data directory (zotero.sqlite + storage/) with LIBRARY in it
    (with groups=True also a group library, GROUP_LIBRARY, and an RSS feed library)."""
    zdir.mkdir(parents=True, exist_ok=True)
    (zdir / "zotero.sqlite").unlink(missing_ok=True)
    db = sqlite3.connect(zdir / "zotero.sqlite")
    db.executescript(SCHEMA)
    db.execute("INSERT INTO version VALUES ('userdata', 122)")
    db.execute("INSERT INTO libraries VALUES (1, 'user', 1, 1, 0, 0, 0, 0)")
    for tid, name in ITEM_TYPES.items():
        db.execute("INSERT INTO itemTypes VALUES (?, ?, NULL, 1)", (tid, name))
        db.execute("INSERT INTO itemTypesCombined VALUES (?, ?, 1, 0)", (tid, name))
    for fid, name in FIELDS.items():
        db.execute("INSERT INTO fields VALUES (?, ?, NULL)", (fid, name))
        db.execute("INSERT INTO fieldsCombined VALUES (?, ?, NULL, NULL, 0)", (fid, name))
    for cid, name in CREATOR_TYPES.items():
        db.execute("INSERT INTO creatorTypes VALUES (?, ?)", (cid, name))
    tid_of = {v: k for k, v in ITEM_TYPES.items()}
    fid_of = {v: k for k, v in FIELDS.items()}
    col_id = {}
    all_cols = [(k, n, p, 1) for k, n, p in COLLECTIONS] + ([(k, n, p, 2) for k, n, p in GROUP_COLLECTIONS] if groups else [])
    for i, (key, name, parent, lib_id) in enumerate(all_cols, start=1):
        col_id[key] = i
        db.execute("INSERT INTO collections (collectionID, collectionName, parentCollectionID, libraryID, key) "
                   "VALUES (?, ?, ?, ?, ?)", (i, name, col_id.get(parent), lib_id, key))
    by_name = {name: col_id[key] for key, name, _, _ in all_cols}
    if groups:
        db.execute("INSERT INTO libraries VALUES (2, 'group', 1, 1, 0, 0, 0, 0)")
        db.execute("INSERT INTO groups VALUES (4242, 2, 'PESPL lab group', '', 0)")
        db.execute("INSERT INTO libraries VALUES (3, 'feed', 0, 0, 0, 0, 0, 0)")
    next_id = [100]
    cur_lib = [1]

    def new_item(type_name: str, key: str, added: str) -> int:
        next_id[0] += 1
        db.execute("INSERT INTO items (itemID, itemTypeID, dateAdded, libraryID, key) VALUES (?, ?, ?, ?, ?)",
                   (next_id[0], tid_of[type_name], added, cur_lib[0], key))
        return next_id[0]

    def set_field(iid: int, field: str, value: str):
        row = db.execute("SELECT valueID FROM itemDataValues WHERE value = ?", (value,)).fetchone()
        vid = row[0] if row else db.execute("INSERT INTO itemDataValues (value) VALUES (?)", (value,)).lastrowid
        db.execute("INSERT INTO itemData VALUES (?, ?, ?)", (iid, fid_of[field], vid))

    def creator(first: str, last: str) -> int:
        mode = 1 if not first else 0
        row = db.execute("SELECT creatorID FROM creators WHERE firstName = ? AND lastName = ? AND fieldMode = ?",
                         (first, last, mode)).fetchone()
        return row[0] if row else db.execute("INSERT INTO creators (firstName, lastName, fieldMode) VALUES (?, ?, ?)",
                                             (first, last, mode)).lastrowid

    for n, p in enumerate(LIBRARY + (GROUP_LIBRARY if groups else [])):
        cur_lib[0] = 2 if p in GROUP_LIBRARY else 1
        added = f"2024-01-{n + 1:02d} 10:00:00"
        iid = new_item(p["type"], p["key"], added)
        for field in ("title", "date", "url", "DOI", "publicationTitle", "proceedingsTitle", "repository",
                      "archiveID", "university", "bookTitle", "extra"):
            if p.get(field):
                set_field(iid, field, p[field])
        if p.get("abstract"):
            set_field(iid, "abstractNote", p["abstract"])
        order = 0
        for first, last in p["authors"]:
            db.execute("INSERT INTO itemCreators VALUES (?, ?, 8, ?)", (iid, creator(first, last), order))
            order += 1
        for first, last in p.get("editors", []):
            db.execute("INSERT INTO itemCreators VALUES (?, ?, 10, ?)", (iid, creator(first, last), order))
            order += 1
        for t in p.get("tags", []):
            row = db.execute("SELECT tagID FROM tags WHERE name = ?", (t,)).fetchone()
            tag = row[0] if row else db.execute("INSERT INTO tags (name) VALUES (?)", (t,)).lastrowid
            db.execute("INSERT INTO itemTags VALUES (?, ?, 0)", (iid, tag))
        for c in p.get("collections", []):
            db.execute("INSERT INTO collectionItems VALUES (?, ?, 0)", (by_name[c], iid))
        if p.get("deleted"):
            db.execute("INSERT INTO deletedItems (itemID) VALUES (?)", (iid,))
        for k, html in enumerate(p.get("notes", [])):
            nid = new_item("note", f"{p['key'][:4]}NOT{k}", added)
            db.execute("INSERT INTO itemNotes VALUES (?, ?, ?, ?)", (nid, iid, html, "note"))
        if p.get("pdf"):
            akey, fname, pages = p["pdf"]
            aid = new_item("attachment", akey, added)
            db.execute("INSERT INTO itemAttachments (itemID, parentItemID, linkMode, contentType, path) "
                       "VALUES (?, ?, 1, 'application/pdf', ?)", (aid, iid, f"storage:{fname}"))
            if pages:
                (zdir / "storage" / akey).mkdir(parents=True, exist_ok=True)
                (zdir / "storage" / akey / fname).write_bytes(make_pdf(p["title"], pages))
                (zdir / "storage" / akey / ".zotero-ft-cache").write_text("cached text")
            for a in p.get("annotations", []):
                anid = new_item("annotation", a["key"], added)
                pos = json.dumps({"pageIndex": a["pageIndex"], "rects": [[72.0, 600.0, 400.0, 612.0]]})
                db.execute("INSERT INTO itemAnnotations (itemID, parentItemID, type, text, comment, color, "
                           "pageLabel, sortIndex, position, isExternal) VALUES (?, ?, ?, ?, ?, '#ffd400', ?, ?, ?, 0)",
                           (anid, aid, a["type"], a["text"], a["comment"], a["pageLabel"],
                            f"{a['pageIndex']:05d}|000100|00200", pos))
    cur_lib[0] = 1
    if groups:  # a feed item: never a paper, never counted
        cur_lib[0] = 3
        fid = new_item("journalArticle", "FEEDITM1", "2024-03-01 10:00:00")
        set_field(fid, "title", "Feed item that should not import")
        cur_lib[0] = 1
    # a standalone note and a standalone attachment (top-level, not papers)
    sid = new_item("note", "STANDNTE", "2024-02-01 10:00:00")
    db.execute("INSERT INTO itemNotes VALUES (?, NULL, '<p>Standalone note</p>', 'Standalone note')", (sid,))
    db.execute("INSERT INTO deletedCollections (collectionID) VALUES (?)", (col_id["COLEMPTY"],))
    db.commit()
    if wal:
        db.execute("PRAGMA journal_mode=WAL")
    db.close()
    return zdir


def _tree_hash(d: Path) -> str:
    h = hashlib.sha1()
    for p in sorted(d.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(d).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()


# ---------------------------------------------------------------- fixtures
@pytest.fixture()
def zdir(tmp_path):
    return build_zotero(tmp_path / "Zotero")


@pytest.fixture()
def env(tmp_path, monkeypatch, zdir):
    monkeypatch.setenv("RB_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("RB_FAKE_CLAUDE", "1")
    monkeypatch.setenv("RB_EMBEDDER", "tfidf")
    monkeypatch.setenv("RB_ZOTERO_DIR", str(zdir))
    for m in [m for m in sys.modules if m.startswith("app")]:
        del sys.modules[m]
    server = importlib.import_module("app.server")

    def no_network(*a, **k):
        raise AssertionError("Zotero import must not call Semantic Scholar")

    for name in ("lookup", "s2_paper", "discover"):
        monkeypatch.setattr(server.scholar, name, no_network)
    from fastapi.testclient import TestClient
    return TestClient(server.app), server


def run_import(client, **body) -> dict:
    payload = {"collection_key": None, "include_pdfs": True, "include_annotations": True, **body}
    r = client.post("/api/zotero/import", json=payload)
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    for _ in range(400):
        job = client.get(f"/api/zotero/jobs/{job_id}").json()
        if job["state"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("import didn't finish")


def papers(server) -> dict:
    return {n["zotero_key"]: n for n in server.store.all() if n["type"] == "paper"}


# ---------------------------------------------------------------- tests
def test_status(env):
    client, _ = env
    st = client.get("/api/zotero/status").json()
    assert st["found"] is True and st["items"] == 5  # deleted item, notes, attachments, annotations excluded
    cols = {c["name"]: c for c in st["collections"]}
    assert set(cols) == {"ML", "Plasma", "Hall thrusters", "Kr CRM"}  # deleted collection hidden
    assert cols["Hall thrusters"]["parentKey"] == "COLPLAS1" and cols["Plasma"]["parentKey"] is None
    assert cols["ML"]["count"] == 1  # the trashed item doesn't count
    assert cols["Plasma"]["count"] == 3  # includes items in sub-collections
    names = [c["name"] for c in st["collections"]]
    assert names.index("Hall thrusters") == names.index("Plasma") + 1  # children follow their parent


def test_status_not_found(env, monkeypatch, tmp_path):
    client, _ = env
    monkeypatch.setenv("RB_ZOTERO_DIR", str(tmp_path / "nowhere"))
    st = client.get("/api/zotero/status").json()
    assert st["found"] is False and "RB_ZOTERO_DIR" in st["message"] and st["collections"] == []
    r = client.post("/api/zotero/import", json={})
    assert r.status_code == 503 and "RB_ZOTERO_DIR" in r.json()["detail"]


def test_import_everything(env, zdir):
    client, server = env
    before = _tree_hash(zdir)
    job = run_import(client)
    assert job["state"] == "done", job
    assert (job["done"], job["total"], job["created"], job["updated"], job["skipped"]) == (5, 5, 5, 0, 0)
    assert job["errors"] == [] and job["fragments"] == 4 and job["pdfs"] == 2
    assert _tree_hash(zdir) == before  # Zotero's files are untouched

    p = papers(server)
    assert set(p) == {"ATTN2017", "HALL2016", "PINN2019", "CRM2021", "BOOKSEC1"}
    attn = p["ATTN2017"]
    assert attn["authors"] == ["Ashish Vaswani", "Noam Shazeer", "Niki Parmar"]  # editors left out, order kept
    assert attn["year"] == 2017 and attn["arxiv"] == "1706.03762"
    assert attn["venue"] == "Advances in Neural Information Processing Systems"
    assert attn["tags"] == ["attention", "transformers"] and attn["summary"] == ""
    assert "## Reading notes" in attn["body"] and "**recurrence**" in attn["body"]
    assert "- Multi-head" in attn["body"] and "*sinusoidal*" in attn["body"]
    assert "[the illustrated version](https://jalammar.github.io/illustrated-transformer/)" in attn["body"]
    hall = p["HALL2016"]
    assert hall["doi"] == "10.1063/1.4945558" and hall["year"] == 2016 and hall["venue"] == "Physics of Plasmas"
    pinn = p["PINN2019"]
    assert pinn["arxiv"] == "1901.01234" and pinn["authors"] == ["Maziar Raissi", "PESPL Consortium"]
    assert pinn["body"] == "Child note one.\n\n1. first\n2. second"
    assert p["CRM2021"]["arxiv"] == "2105.00001" and p["CRM2021"]["venue"] == "Oregon State University"
    assert p["BOOKSEC1"]["venue"] == "Principles of Plasma Diagnostics" and not p["BOOKSEC1"]["has_pdf"]
    assert not any("Deleted" in n["title"] or n["title"] == "Standalone note" for n in server.store.all())

    # PDF copied into the vault, text extracted for Claude
    assert attn["has_pdf"] and attn["has_text"]
    assert "multi-head self attention" in server.store.text_path(attn["id"]).read_text()
    assert client.get(f"/pdf/{attn['id']}").headers["content-type"] == "application/pdf"

    # graph works after a bulk import (index rebuilt lazily once)
    g = client.get("/api/graph").json()
    assert len([n for n in g["nodes"] if n["type"] == "paper"]) == 5


def test_annotations_become_fragments(env):
    client, server = env
    run_import(client)
    attn = papers(server)["ATTN2017"]
    note = client.get(f"/api/notes/{attn['id']}").json()
    frags = note["fragments"]
    assert [f["page"] for f in frags] == [1, 4, 5]  # ink annotation without comment skipped
    assert frags[0]["quote"].startswith("We propose a new simple network") and frags[0]["body"] == "The core claim."
    assert frags[0]["locator"] == "p. 1"
    assert frags[1]["quote"] == "Attention(Q, K, V) = softmax(QK^T / √d_k) V"
    assert frags[1]["body"] == "Scaling keeps softmax\nout of saturation."
    assert frags[2]["quote"] == "" and frags[2]["body"] == "Compare with linear attention later."
    stored = server.store.get(frags[0]["id"])
    assert stored["parent"] == attn["id"] and stored["zotero_key"] == "ANNA0001"
    hall = papers(server)["HALL2016"]
    hf = server.store.children(hall["id"])
    assert hf[0]["locator"] == "p. ii" and hf[0]["page"] == 2  # page label vs page index
    g = client.get("/api/graph").json()
    assert sum(1 for l in g["links"] if l["kind"] == "part") == 4


def test_reimport_is_idempotent(env):
    client, server = env
    run_import(client)
    n1 = len(server.store.all())
    job = run_import(client)
    assert (job["created"], job["updated"], job["skipped"], job["fragments"], job["pdfs"]) == (0, 0, 5, 0, 0)
    assert len(server.store.all()) == n1


def test_dedupe_preserves_user_work(env):
    client, server = env
    s = server.store
    # Existing notes the user already has: one by DOI (with their own summary/body), one by title, one by arXiv
    mine = s.create({"type": "paper", "title": "Breathing mode paper", "doi": "10.1063/1.4945558",
                     "summary": "My summary.", "body": "My own notes."})
    by_title = s.create({"type": "paper", "title": "attention is all you need!", "tags": ["mine"]})
    s.create({"type": "fragment", "parent": by_title["id"], "quote": "We propose a new simple network architecture, "
              "the Transformer,", "body": "I already clipped this."})
    by_arx = s.create({"type": "paper", "title": "PINNs", "arxiv": "1901.01234"})
    job = run_import(client)
    assert job["created"] == 2 and job["updated"] == 3, job
    h = s.get(mine["id"])
    assert h["summary"] == "My summary." and h["body"] == "My own notes." and h["title"] == "Breathing mode paper"
    assert h["has_pdf"] and h["zotero_key"] == "HALL2016" and h["venue"] == "Physics of Plasmas"
    assert len(s.children(mine["id"])) == 1
    a = s.get(by_title["id"])
    assert a["has_pdf"] and a["zotero_key"] == "ATTN2017" and a["tags"][0] == "mine"
    assert "Reading notes" in a["body"]  # body was empty, so the Zotero note fills it
    quotes = [f["quote"] for f in s.children(a["id"])]
    assert len(quotes) == 3 and quotes.count(quotes[0]) == 1  # the already-clipped highlight isn't duplicated
    p = s.get(by_arx["id"])
    assert p["zotero_key"] == "PINN2019" and p["body"].startswith("Child note one.")
    assert len([n for n in s.all() if n["type"] == "paper"]) == 5
    # PDF already present is not replaced
    s.pdf_path(mine["id"]).write_bytes(b"%PDF-1.4 user's own copy")
    run_import(client)
    assert s.pdf_path(mine["id"]).read_bytes() == b"%PDF-1.4 user's own copy"


def test_collection_and_options(env):
    client, server = env
    job = run_import(client, collection_key="COLPLAS1", include_pdfs=False, include_annotations=False)
    assert job["total"] == 3 and job["created"] == 3 and job["fragments"] == 0 and job["pdfs"] == 0
    assert set(papers(server)) == {"HALL2016", "PINN2019", "CRM2021"}
    assert not any(n["has_pdf"] for n in server.store.all())
    # then turning the options on for the same collection adds PDFs + highlights to the existing notes
    job = run_import(client, collection_key="COLHALL1")
    assert job["total"] == 1 and job["updated"] == 1 and job["fragments"] == 1 and job["pdfs"] == 1
    assert client.post("/api/zotero/import", json={"collection_key": "NOPE"}).status_code == 404
    assert client.get("/api/zotero/jobs/doesnotexist").status_code == 404


def test_reads_wal_while_zotero_is_open(tmp_path, monkeypatch):
    """Zotero runs in WAL mode and keeps the database open; recent writes live in zotero.sqlite-wal."""
    zdir = build_zotero(tmp_path / "Zotero", wal=True)
    live = sqlite3.connect(zdir / "zotero.sqlite")
    live.execute("PRAGMA wal_autocheckpoint=0")
    live.execute("INSERT INTO items (itemID, itemTypeID, libraryID, key) VALUES (9999, 22, 1, 'WALITEM1')")
    live.execute("INSERT INTO itemDataValues (value) VALUES ('Only in the WAL')")
    live.execute("INSERT INTO itemData VALUES (9999, 1, (SELECT valueID FROM itemDataValues WHERE value='Only in the WAL'))")
    live.commit()
    assert (zdir / "zotero.sqlite-wal").stat().st_size > 0
    monkeypatch.setenv("RB_ZOTERO_DIR", str(zdir))
    for m in [m for m in sys.modules if m.startswith("app")]:
        del sys.modules[m]
    zotero = importlib.import_module("app.zotero")
    lib = zotero.read_library(zdir)
    assert "Only in the WAL" in [it["title"] for it in lib["items"]]
    live.close()


def _zedit(zdir: Path, sql: str, *args):
    """Change the fake Zotero library in place (as the user would, in Zotero)."""
    with sqlite3.connect(zdir / "zotero.sqlite") as db:
        db.execute(sql, args)


def test_group_libraries(tmp_path, monkeypatch):
    zdir = build_zotero(tmp_path / "Zotero", groups=True)
    monkeypatch.setenv("RB_ZOTERO_DIR", str(zdir))
    monkeypatch.setenv("RB_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("RB_FAKE_CLAUDE", "1")
    monkeypatch.setenv("RB_EMBEDDER", "tfidf")
    for m in [m for m in sys.modules if m.startswith("app")]:
        del sys.modules[m]
    server = importlib.import_module("app.server")
    from fastapi.testclient import TestClient
    client = TestClient(server.app)
    st = client.get("/api/zotero/status").json()
    assert st["libraries"] == [{"id": 1, "name": "My Library", "kind": "user", "items": 5},
                               {"id": 2, "name": "PESPL lab group", "kind": "group", "items": 1}]  # feed left out
    assert st["items"] == 6
    grp = next(c for c in st["collections"] if c["key"] == "COLGRP01")
    assert grp["library_id"] == 2 and grp["count"] == 1
    assert next(c for c in st["collections"] if c["key"] == "COLML001")["library_id"] == 1
    # import just the group library
    job = run_import(client, library_id=2)
    assert job["total"] == 1 and job["created"] == 1 and job["fragments"] == 1 and job["library_id"] == 2
    assert set(papers(server)) == {"GRPLASMA"}
    # a collection must belong to the chosen library; unknown libraries are a 404
    r = client.post("/api/zotero/import", json={"library_id": 2, "collection_key": "COLML001"})
    assert r.status_code == 404 and "collection" in r.json()["detail"]
    r = client.post("/api/zotero/import", json={"library_id": 3})
    assert r.status_code == 404 and "library" in r.json()["detail"]
    job = run_import(client)  # everything: the user library joins, the group paper is skipped
    assert job["created"] == 5 and job["skipped"] == 1
    assert "Feed item that should not import" not in {n["title"] for n in server.store.all()}


def test_annotation_sync_on_reimport(env, zdir):
    client, server = env
    run_import(client)
    s = server.store
    attn = papers(server)["ATTN2017"]
    frags = {f["zotero_annotation"]: f for f in s.children(attn["id"])}
    assert set(frags) == {"ANNA0001", "ANNA0002", "ANNA0003"} and all(f["zotero_hash"] for f in frags.values())
    # the user edits one fragment's note in research_brain (it must then never be changed or removed)
    s.update(frags["ANNA0003"]["id"], {"body": "Compare with linear attention later. My own thought: Performer."})
    # ...and in Zotero: one comment edited, one highlight moved page, two highlights deleted
    _zedit(zdir, "UPDATE itemAnnotations SET comment = 'The core claim, revised.' WHERE itemID = "
                 "(SELECT itemID FROM items WHERE key = 'ANNA0001')")
    _zedit(zdir, "UPDATE itemAnnotations SET pageLabel = '5', position = ? WHERE itemID = "
                 "(SELECT itemID FROM items WHERE key = 'ANNA0002')", json.dumps({"pageIndex": 4, "rects": []}))
    _zedit(zdir, "DELETE FROM itemAnnotations WHERE itemID IN (SELECT itemID FROM items WHERE key IN ('ANNA0003', 'ANNH0001'))")
    job = run_import(client)
    assert job["state"] == "done", job
    assert (job["highlights_updated"], job["highlights_removed"], job["highlights_kept"]) == (2, 1, 1), job
    assert job["kept"][0]["id"] == frags["ANNA0003"]["id"]  # reported as "kept (edited)"
    now = {f.get("zotero_annotation"): f for f in s.children(attn["id"])}
    assert now["ANNA0001"]["body"] == "The core claim, revised."
    assert now["ANNA0002"]["page"] == 5 and now["ANNA0002"]["locator"] == "p. 5"
    assert now["ANNA0003"]["body"].endswith("Performer.")  # edited by the user: kept although deleted in Zotero
    hall = papers(server)["HALL2016"]
    assert s.children(hall["id"]) == []  # unedited + deleted in Zotero -> moved to the trash
    assert any(p.name.startswith(hall["id"]) for p in s.trash_dir.iterdir())
    # a second re-import is quiet
    job = run_import(client)
    assert (job["highlights_updated"], job["highlights_removed"], job["fragments"]) == (0, 0, 0)
    # the user edits a fragment and Zotero edits it too: the user's version wins, and it's reported
    s.update(now["ANNA0001"]["id"], {"body": "My rewrite."})
    _zedit(zdir, "UPDATE itemAnnotations SET comment = 'Zotero again.' WHERE itemID = "
                 "(SELECT itemID FROM items WHERE key = 'ANNA0001')")
    job = run_import(client)
    assert s.get(now["ANNA0001"]["id"])["body"] == "My rewrite."
    assert any(k["id"] == now["ANNA0001"]["id"] for k in job["kept"])


def test_html_to_markdown():
    from app.zotero import html_to_markdown
    assert html_to_markdown("<p>a &amp; b</p><p>c<br>d</p>") == "a & b\n\nc\nd"
    assert html_to_markdown("<ul><li>x<ul><li>y</li></ul></li><li>z</li></ul>") == "- x\n  - y\n- z"
    assert html_to_markdown("<blockquote><p>quoted</p></blockquote>") == "> quoted"
    assert html_to_markdown("") == ""


if __name__ == "__main__":
    out = build_zotero(Path(sys.argv[1]).expanduser())
    print(f"Fake Zotero library written to {out}")
