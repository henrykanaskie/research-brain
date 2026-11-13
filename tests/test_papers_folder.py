"""One copy of each PDF, in one folder you can find (app/papers.py)."""
import pytest

from test_api import client, make_pdf  # noqa: F401  (fixture)

pytest.importorskip("reportlab")


@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / "Downloads").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    trashed = []

    def fake_trash(p):  # the computer's Trash, recorded instead of touched
        import os
        trashed.append(str(p))
        os.remove(p)
    import send2trash
    monkeypatch.setattr(send2trash, "send2trash", fake_trash)
    return h, trashed


def _pdf(name="A study of sheath physics"):
    return make_pdf(name, "Ions enter the sheath at the Bohm speed.\nThis is a unique body for " + name)


def test_same_file_is_never_stored_twice(client, home):
    h, trashed = home
    pdf = _pdf()
    (h / "Downloads" / "sheath.pdf").write_bytes(pdf)
    a = client.post("/api/import/pdf", files={"file": ("sheath.pdf", pdf, "application/pdf")}).json()
    assert a["downloads_copy"] == str(h / "Downloads" / "sheath.pdf")  # the copy it came from, offered for the Trash
    b = client.post("/api/import/pdf", files={"file": ("renamed.pdf", pdf, "application/pdf")}).json()
    assert b["id"] == a["id"] and b["duplicate"] and b["same_file"]
    from app import server
    assert len(list(server.store.pdf_dir.glob("*.pdf"))) == 1
    r = client.post("/api/papers/trash-copies", json={"paths": [a["downloads_copy"]]}).json()
    assert r["trashed"] == [a["downloads_copy"]] and not (h / "Downloads" / "sheath.pdf").exists()
    # something that isn't a copy of a library PDF is refused
    (h / "Downloads" / "other.pdf").write_bytes(_pdf("Something else"))
    r = client.post("/api/papers/trash-copies", json={"paths": [str(h / "Downloads" / "other.pdf")]}).json()
    assert r["refused"] and (h / "Downloads" / "other.pdf").exists()


def test_move_the_papers_folder(client, home, tmp_path):
    h, _ = home
    from app import server
    a = client.post("/api/import/pdf", files={"file": ("a.pdf", _pdf(), "application/pdf")}).json()
    f = client.get("/api/papers-folder").json()
    assert f["default"] and f["pdfs"] == 1
    dest = h / "Documents" / "Research papers"
    r = client.put("/api/papers-folder", json={"folder": str(dest)}).json()
    assert r["moved"] == 1 and r["folder"] == str(dest.resolve()) and not r["default"]
    assert (dest / f"{a['id']}.pdf").is_file() and server.store.pdf_path(a["id"]).parent == dest.resolve()
    assert client.get(f"/api/notes/{a['id']}").json()["has_pdf"]
    # the choice survives a restart: a new Store reads it
    from app.store import Store
    assert Store(server.store.root).pdf_dir == dest.resolve()
    assert client.put("/api/papers-folder", json={"folder": "relative/path"}).status_code == 400
    assert client.put("/api/papers-folder", json={"folder": str(server.store.root / "state")}).status_code == 400


def test_delete_keeps_or_trashes_the_pdf(client, home):
    h, trashed = home
    from app import server
    a = client.post("/api/import/pdf", files={"file": ("a.pdf", _pdf("Paper one"), "application/pdf")}).json()
    b = client.post("/api/import/pdf", files={"file": ("b.pdf", _pdf("Paper two"), "application/pdf")}).json()
    r = client.delete(f"/api/notes/{a['id']}?pdf=keep").json()
    kept = server.store.pdf_dir / "Removed from library" / f"{a['id']}.pdf"
    assert r["pdf"] == "kept" and kept.is_file()
    path_b = str(server.store.pdf_path(b["id"]))
    r = client.delete(f"/api/notes/{b['id']}?pdf=trash").json()
    assert r["pdf"] == "trash" and trashed == [path_b]
    assert client.get("/api/papers-folder").json()["removed"] == 1
    assert client.delete("/api/notes/x?pdf=bogus").status_code in (400, 404)


def test_check_a_folder_and_add_what_is_missing(client, home):
    h, trashed = home
    known, new1, new2 = _pdf("Known paper"), _pdf("New paper one"), _pdf("New paper two")
    client.post("/api/import/pdf", files={"file": ("k.pdf", known, "application/pdf")})
    d = h / "Downloads"
    (d / "known copy.pdf").write_bytes(known)
    (d / "new1.pdf").write_bytes(new1)
    (d / "sub").mkdir()
    (d / "sub" / "new2.pdf").write_bytes(new2)
    r = client.post("/api/papers/scan", json={"folder": str(d)}).json()
    assert [x["name"] for x in r["in_library"]] == ["known copy.pdf"] and [x["name"] for x in r["new"]] == ["new1.pdf"]
    r = client.post("/api/papers/scan", json={"folder": str(d), "recursive": True}).json()
    assert sorted(x["name"] for x in r["new"]) == ["new1.pdf", "new2.pdf"]
    assert client.post("/api/papers/scan", json={"folder": "/etc"}).status_code == 400  # outside your home folder
    # add: move (the original goes to the Trash once the library has it) and copy (the original stays)
    a = client.post("/api/papers/import-files", json={"paths": [str(d / "new1.pdf")], "mode": "move"}).json()
    assert len(a["added"]) == 1 and trashed == [str(d / "new1.pdf")]
    a = client.post("/api/papers/import-files", json={"paths": [str(d / "sub" / "new2.pdf")], "mode": "copy"}).json()
    assert len(a["added"]) == 1 and (d / "sub" / "new2.pdf").exists()
    # adding a copy of something you have adds nothing
    a = client.post("/api/papers/import-files", json={"paths": [str(d / "known copy.pdf")], "mode": "copy"}).json()
    assert a["added"] == [] and len(a["duplicates"]) == 1
    from app import server
    assert len(list(server.store.pdf_dir.glob("*.pdf"))) == 3


def test_zip_export_takes_pdfs_from_the_papers_folder(client, home, tmp_path):
    h, _ = home
    import zipfile
    from app import server, vaultzip
    a = client.post("/api/import/pdf", files={"file": ("a.pdf", _pdf(), "application/pdf")}).json()
    client.put("/api/papers-folder", json={"folder": str(h / "Papers")})
    client.delete(f"/api/notes/{a['id']}?pdf=keep")
    b = client.post("/api/import/pdf", files={"file": ("b.pdf", _pdf("Second"), "application/pdf")}).json()
    out = tmp_path / "lib.zip"
    m = vaultzip.export_zip(server.store.root, out)
    names = zipfile.ZipFile(out).namelist()
    assert f"PDFs/{b['id']}.pdf" in names and m["pdfs"] == 1
    assert "state/papers.json" not in names and not any("Removed from library" in n for n in names)


def test_quick_import_into_a_topic_and_bookmarks(client, home):
    from app import server, topics as T
    a = client.post("/api/import/pdf?quick=1&topic=Plasma%20sheaths", files={"file": ("a.pdf", _pdf("Sheath one"), "application/pdf")}).json()
    b = client.post("/api/import/pdf?quick=1&topic=plasma%20SHEATHS", files={"file": ("b.pdf", _pdf("Sheath two"), "application/pdf")}).json()
    tops = T.list_topics(server.store)
    assert len(tops) == 1 and tops[0]["name"] == "Plasma sheaths" and a["topic"] == b["topic"] == tops[0]["slug"]
    assert set(T.get_topic(server.store, tops[0]["slug"])["members"]) == {a["id"], b["id"]}
    # bookmarks: a DOI link and an arXiv link become papers from their records (no download); a plain link a paper
    r1 = client.post("/api/import/bookmark", json={"url": "https://pubs.aip.org/aip/pop/article/doi/10.1063/5.0010332/123",
                                                   "title": "Some AIP paper", "topic": "Hall thrusters"}).json()
    n1 = client.get(f"/api/notes/{r1['id']}").json()
    assert r1["kind"] == "record" and n1["doi"] == "10.1063/5.0010332"
    r2 = client.post("/api/import/bookmark", json={"url": "https://arxiv.org/abs/1706.03762v5", "title": "Attention", "topic": "Hall thrusters"}).json()
    assert client.get(f"/api/notes/{r2['id']}").json()["arxiv"] == "1706.03762"
    again = client.post("/api/import/bookmark", json={"url": "https://arxiv.org/pdf/1706.03762", "title": "x"}).json()
    assert again["duplicate"] and again["id"] == r2["id"]
    r3 = client.post("/api/import/bookmark", json={"url": "https://lab.example.edu/notes/thruster-tips", "title": "Thruster tips"}).json()
    assert r3["kind"] == "link"
    assert client.post("/api/import/bookmark", json={"url": "javascript:alert(1)", "title": "x"}).status_code == 400
    hall = next(t for t in T.list_topics(server.store) if t["name"] == "Hall thrusters")
    assert set(T.get_topic(server.store, hall["slug"])["members"]) == {r1["id"], r2["id"]}


def test_pdf_with_highlights_has_real_annotations(client, home):
    import io
    from pypdf import PdfReader
    body = ("Ions enter the sheath at the Bohm speed.\nThe presheath accelerates them over many Debye lengths.\n"
            "Secondary electron emission from the walls changes the potential drop.")
    a = client.post("/api/import/pdf", files={"file": ("a.pdf", make_pdf("Sheath physics", body), "application/pdf")}).json()
    client.post("/api/notes", json={"type": "fragment", "parent": a["id"], "page": 1, "quote": "Ions enter the sheath at the Bohm speed.",
                                    "body": "Key condition for my model"})
    client.post("/api/notes", json={"type": "fragment", "parent": a["id"], "page": 1, "quote": "the potential   drop"})
    client.post("/api/notes", json={"type": "fragment", "parent": a["id"], "page": 1, "quote": "A sentence that is nowhere in the paper at all"})
    r = client.get(f"/pdf/{a['id']}/annotated")
    assert r.status_code == 200 and r.headers["x-rb-placed"] == "2" and r.headers["x-rb-missed"] == "1"
    page = PdfReader(io.BytesIO(r.content)).pages[0]
    annots = [x.get_object() for x in page["/Annots"]]
    hl = [x for x in annots if x["/Subtype"] == "/Highlight"]
    assert len(hl) == 2 and any(x.get("/Contents") == "Key condition for my model" for x in hl)
    assert all(len(x["/QuadPoints"]) % 8 == 0 for x in hl)
    assert any(x["/Subtype"] == "/FreeText" and "couldn't be placed" in x.get("/Contents", "") for x in annots)
    # the library's copy is untouched
    from app import server
    assert "/Annots" not in PdfReader(str(server.store.pdf_path(a["id"]))).pages[0]


def test_find_range_ignores_whitespace_and_hyphenation():
    from app.annotate_pdf import find_range
    page = "The breathing mode is an ioniza-\r\ntion instability whose frequency\r\nscales with the neutral velocity."
    a, b = find_range(page, "an ionization instability whose frequency scales")
    assert page[a:b].startswith("an ioniza") and page[a:b].endswith("scales")
    assert find_range(page, "nothing like this") is None
