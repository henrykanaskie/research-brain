"""Readable folders (app/layout.py): papers, notes, questions and highlights as files named by their titles,
in folders by type; the documents folder (app/paths.py); and the one-time move from notes/<id>.md
(app/migrate.py, schema 2).

Run:  .venv/bin/python -m pytest -q tests/test_layout.py
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("RB_DATA_DIR", str(tmp_path / "data"))
    from app.store import Store
    return Store(tmp_path / "lib")


def rel(store, nid):
    return store.note_path(nid).relative_to(store.docs).as_posix()


def test_each_kind_has_its_folder_and_a_readable_file(store):
    p = store.create({"type": "paper", "title": "Helium line ratios", "year": 1996, "authors": ["A. One", "B. Two"]})
    n = store.create({"type": "note", "title": "My idea: plasma / CRM?"})
    q = store.create({"type": "question", "title": "Why are cross sections useful?"})
    f = store.create({"type": "fragment", "title": "p.3", "parent": p["id"], "quote": "line intensity ratios", "page": 3})
    assert rel(store, p["id"]) == "Papers/Helium line ratios (1996).md"
    assert rel(store, n["id"]) == "Notes/My idea plasma CRM.md"
    assert rel(store, q["id"]) == "Questions/Why are cross sections useful.md"
    assert rel(store, f["id"]) == "Highlights/Helium line ratios (1996)/p.3 line intensity ratios.md"
    text = store.note_path(p["id"]).read_text(encoding="utf-8")
    lines = text.split("\n")
    assert lines[1] == f"id: {p['id']}" and "authors: [A. One, B. Two]" in lines and "{" not in lines[1]
    assert "links:" not in text and "doi:" not in text and "abstract:" not in text  # empty fields left out
    got = store.get(p["id"])
    assert got["links"] == [] and got["doi"] == "" and got["authors"] == ["A. One", "B. Two"]


def test_renaming_moves_the_file_and_the_highlights_follow(store):
    p = store.create({"type": "paper", "title": "Old title", "year": 2020})
    f = store.create({"type": "fragment", "title": "p.1", "parent": p["id"], "quote": "a passage", "page": 1})
    n = store.create({"type": "note", "title": "Linked note"})
    store.link(n["id"], p["id"])
    store.update(p["id"], {"title": "New title"})
    assert rel(store, p["id"]) == "Papers/New title (2020).md"
    assert rel(store, f["id"]) == "Highlights/New title (2020)/p.1 a passage.md"
    assert not (store.docs / "Papers" / "Old title (2020).md").exists()
    assert not (store.docs / "Highlights" / "Old title (2020)").exists()
    assert store.get(f["id"])["parent"] == p["id"] and store.get(n["id"])["links"] == [p["id"]]
    store.update(p["id"], {"title": "new title"})  # only the case changes (one file on macOS and Windows)
    assert store.note_path(p["id"]).name == "new title (2020).md" and store.get(p["id"])["title"] == "new title"
    assert len(list((store.docs / "Papers").glob("*.md"))) == 1


def test_your_own_subfolders_are_kept(store):
    n = store.create({"type": "note", "title": "Filed away"})
    sub = store.docs / "Notes" / "Thesis"
    sub.mkdir()
    store.note_path(n["id"]).replace(sub / "Filed away.md")
    store.update(n["id"], {"title": "Filed and renamed"})
    assert rel(store, n["id"]) == "Notes/Thesis/Filed and renamed.md"


def test_files_made_outside_the_app(store):
    (store.notes_dir / "hand-written.md").write_text("---\ntitle: Hand written\n---\nbody\n", encoding="utf-8")
    assert store.get("hand-written")["title"] == "Hand written"  # an id from the file name, at once
    store.update("hand-written", {"body": "edited in the app"})
    assert "id: hand-written\n" in store.note_path("hand-written").read_text(encoding="utf-8")
    # a copy of a note (same id) doesn't take over the original or break anything
    a = store.create({"type": "note", "title": "Original"})
    (store.notes_dir / "Original copy.md").write_text(store.note_path(a["id"]).read_text(encoding="utf-8"))
    assert store.note_path(a["id"]).name == "Original.md" and len(store.all()) == 2


def test_deleting_tidies_an_empty_highlights_folder(store):
    p = store.create({"type": "paper", "title": "P"})
    f = store.create({"type": "fragment", "title": "p.1", "parent": p["id"], "quote": "q", "page": 1})
    folder = store.note_path(f["id"]).parent
    store.delete(f["id"])
    assert not folder.exists() and store.exists(p["id"])


def test_safe_names():
    from app.layout import safe_name
    assert safe_name('a/b\\c:d*e?f"g<h>i|j#k^l[m]') == "a b c d e f g h i j k l m"
    assert safe_name("  ...  ") == "Untitled" and safe_name("CON") == "CON note"
    long = safe_name("word " * 40)
    assert len(long) <= 90 and not long.endswith(" ")


def test_the_documents_folder(tmp_path, monkeypatch):
    from app import paths
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    for k in ("RB_DATA_DIR", "RB_VAULT", "RB_DOCUMENTS_DIR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(paths, "user_data_dir", lambda: tmp_path / "data")
    default = tmp_path / "data" / "profiles" / "default"
    assert paths.documents_dir(default) == home / "Documents" / "research brain"
    assert paths.documents_dir(tmp_path / "data" / "profiles" / "thesis") == home / "Documents" / "research brain (thesis)"
    (home / "Documents" / "research brain").mkdir()
    (home / "Documents" / "research brain" / "someone else's.txt").write_text("x")
    assert paths.documents_dir(default) == home / "Documents" / "research brain 2"  # never mixed into another folder
    paths.remember_documents_dir(default, home / "Documents" / "research brain 2")
    assert paths.documents_dir(default) == home / "Documents" / "research brain 2"  # recorded: stays put
    assert json.loads((home / "Documents" / "research brain 2" / paths.DOCS_MARKER).read_text())["library"] == str(default)
    assert paths.documents_dir(tmp_path / "elsewhere") == tmp_path / "elsewhere"  # a library in a folder you chose
    monkeypatch.setenv("RB_DOCUMENTS_DIR", str(tmp_path / "chosen"))
    assert paths.documents_dir(tmp_path / "new") == tmp_path / "chosen"


def _old_library(v: Path) -> dict:
    """A schema-1 library: notes/<id>.md, topics/, pdfs/."""
    files = {
        "notes/plasma-paper-1996.md": "---\ntype: paper\ntitle: Plasma paper\nyear: 1996\nlinks: [an-idea]\n---\nmy notes\n",
        "notes/plasma-paper-1996-p-2.md": "---\ntype: fragment\ntitle: p.2\nparent: plasma-paper-1996\nquote: a key passage\n---\nwhy\n",
        "notes/an-idea.md": "---\ntype: note\ntitle: An idea\nlinks: [plasma-paper-1996]\n---\nthinking\n",
        "notes/a-question.md": "---\ntype: question\ntitle: A question?\nquestion_status: open\n---\n",
        "notes/broken.md": "---\ntitle: [unclosed\n---\nkept as it is\n",
        "notes/figure.png": "an image",
        "topics/plasma.md": "---\nname: Plasma\n---\n- [[plasma-paper-1996]]\n",
        "pdfs/plasma-paper-1996.pdf": "%PDF-1.4 x",
    }
    for name, text in files.items():
        (v / name).parent.mkdir(parents=True, exist_ok=True)
        (v / name).write_text(text, encoding="utf-8")
    (v / "state").mkdir(parents=True, exist_ok=True)
    (v / "state" / "meta.json").write_text(json.dumps({"schema": 1, "migrations": []}))
    return files


def test_migration_keeps_every_file_and_its_text(tmp_path, monkeypatch):
    """Regression: on a case-insensitive disk notes/ and Notes/ are one folder; an early version of this
    migration took files for their own copies and deleted them."""
    monkeypatch.setenv("RB_DATA_DIR", str(tmp_path / "data"))
    from app import migrate
    v = tmp_path / "lib"
    files = _old_library(v)
    res = migrate.run(v, log=lambda m: None)
    assert res["applied"] == [2] and res["backup"]
    got = {p.relative_to(v).as_posix(): p for p in v.rglob("*") if p.is_file()
           and p.relative_to(v).parts[0] in ("Papers", "Notes", "Questions", "Highlights", "Topics", "PDFs")}
    assert set(got) == {"Papers/Plasma paper (1996).md", "Highlights/Plasma paper (1996)/p.2 a key passage.md",
                        "Notes/An idea.md", "Questions/A question.md", "Notes/broken.md", "Notes/figure.png",
                        "Topics/plasma.md", "PDFs/plasma-paper-1996.pdf"}
    assert got["Papers/Plasma paper (1996).md"].read_text() == "---\nid: plasma-paper-1996\n" + files["notes/plasma-paper-1996.md"][4:]
    assert got["Notes/broken.md"].read_text() == "---\nid: broken\n" + files["notes/broken.md"][4:]
    assert got["PDFs/plasma-paper-1996.pdf"].read_text() == "%PDF-1.4 x" and got["Notes/figure.png"].read_text() == "an image"
    assert not any(n in os.listdir(v) for n in ("notes", "topics", "pdfs", ".v1-notes", ".v1-topics", ".v1-pdfs"))
    from app.store import Store
    s = Store(v)
    assert {n["id"] for n in s.all()} == {"plasma-paper-1996", "plasma-paper-1996-p-2", "an-idea", "a-question", "broken"}
    assert s.get("an-idea")["links"] == ["plasma-paper-1996"] and s.get("plasma-paper-1996")["has_pdf"]
    assert migrate.run(v, log=lambda m: None)["applied"] == []


def test_an_interrupted_migration_resumes(tmp_path, monkeypatch):
    monkeypatch.setenv("RB_DATA_DIR", str(tmp_path / "data"))
    from app import migrate
    v = tmp_path / "lib"
    _old_library(v)
    calls = {"n": 0}
    real = Path.unlink

    def stop_after_first(self, *a, **k):  # power cut right after the first copy is written
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "unlink", stop_after_first)
    with pytest.raises(KeyboardInterrupt):
        migrate.run(v, log=lambda m: None)
    monkeypatch.setattr(Path, "unlink", real)
    assert migrate.run(v, log=lambda m: None)["applied"] == [2]
    from app.store import Store
    s = Store(v)
    assert len(s.all()) == 5 and len(list((v / "Notes").glob("An idea*.md"))) == 1  # no duplicates


from test_api import client  # noqa: E402,F401  (fixture)


def test_library_settings_show_the_documents_folder(client):  # noqa: F811
    client.post("/api/notes", json={"type": "paper", "title": "A paper"})
    client.post("/api/notes", json={"type": "question", "title": "A question"})
    d = client.get("/api/documents-folder").json()
    assert d["papers"] == 1 and d["questions"] == 1 and d["notes"] == 0
    assert (Path(d["folder"]) / "Papers" / "A paper.md").exists()


@pytest.mark.skipif(not __import__("shutil").which("git"), reason="git not installed")
def test_backup_covers_the_documents_folder(tmp_path, monkeypatch):
    """research-brain backup: the notes live in the documents folder, so it's snapshotted too (PDFs left out)."""
    import subprocess
    monkeypatch.setenv("RB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RB_DOCUMENTS_DIR", str(tmp_path / "docs"))
    from app import backup
    from app.store import Store
    s = Store(tmp_path / "lib")
    s.create({"type": "note", "title": "Backed up"})
    (s.docs / "PDFs" / "big.pdf").write_bytes(b"%PDF")
    out = backup.snapshot_library(tmp_path / "lib")
    assert out.count("Saved snapshot") == 2
    files = subprocess.run(["git", "-C", str(s.docs), "ls-files"], capture_output=True, text=True).stdout.split("\n")
    assert "Notes/Backed up.md" in files and not any(f.startswith("PDFs/") for f in files)


def test_obsidian_style_links_by_file_name_resolve(client):  # noqa: F811
    """[[Title (1996)]] (a paper's file name, how Obsidian links) finds the paper in the app too."""
    p = client.post("/api/notes", json={"type": "paper", "title": "Helium ratios", "year": 1996}).json()
    n = client.post("/api/notes", json={"type": "note", "title": "Linker", "body": "see [[Helium ratios (1996)]]"}).json()
    links = client.get("/api/graph").json()["links"]
    assert any(l["kind"] == "mention" and {l["source"], l["target"]} == {n["id"], p["id"]} for l in links)
