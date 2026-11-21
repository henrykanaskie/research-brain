"""Running out of disk space, and new PDFs kept online only in a cloud papers folder (app/diskspace.py); online-only
PDFs in OneDrive or iCloud Drive are never read in the background, since reading one downloads it (app/acquire.py);
and on Windows the documents folder follows OneDrive's Documents (app/paths.py)."""
import errno
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import acquire, diskspace, paths

from test_api import client, make_pdf  # noqa: F401  (fixture)


def test_running_out_of_space_is_caught_before_and_during_a_write(tmp_path, monkeypatch):
    """ensure_room keeps MIN_FREE free and says how much is left; a full disk is recognised from the OS, from
    SQLite and through a chained exception (not from other errors); a copy that fails halfway leaves no file."""
    monkeypatch.setattr(diskspace, "free_bytes", lambda p: 500 * diskspace.MB)
    monkeypatch.setattr(diskspace, "MIN_FREE", 300 * diskspace.MB)
    diskspace.ensure_room(tmp_path, 100 * diskspace.MB)
    with pytest.raises(diskspace.DiskFull) as e:
        diskspace.ensure_room(tmp_path, 250 * diskspace.MB)
    assert "500 MB free" in str(e.value) and "OneDrive" in str(e.value)

    assert diskspace.is_disk_full(OSError(errno.ENOSPC, "No space left on device"))
    assert diskspace.is_disk_full(sqlite3.OperationalError("database or disk is full"))
    try:
        try:
            raise OSError(errno.ENOSPC, "full")
        except OSError as inner:
            raise RuntimeError("saving failed") from inner
    except RuntimeError as outer:
        assert diskspace.is_disk_full(outer)
    assert not diskspace.is_disk_full(OSError(errno.EACCES, "denied"))

    src = tmp_path / "in.pdf"
    src.write_bytes(b"%PDF-1.4 " + b"x" * 1000)
    dest = tmp_path / "papers" / "p.pdf"

    def full(a, b, **kw):
        Path(b).write_bytes(b"half")
        raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(diskspace.shutil, "copy2", full)
    with pytest.raises(OSError):
        diskspace.put_file(src, dest, copy=True)
    assert not dest.exists() and list(dest.parent.iterdir()) == [] and src.exists()


def test_cloud_folders_online_only_files_and_the_documents_folder_are_recognised(monkeypatch, tmp_path):
    """Online-only files on macOS (SF_DATALESS) and Windows (Files On-Demand, offline) are told from normal
    ones; the documents folder is the real one on Windows and ~/Documents elsewhere; OneDrive, Dropbox and
    iCloud Drive folders are named by their provider."""
    assert acquire.cloud_only(SimpleNamespace(st_flags=0x40000000))            # macOS SF_DATALESS
    assert acquire.cloud_only(SimpleNamespace(st_file_attributes=0x00400000))  # OneDrive Files On-Demand
    assert acquire.cloud_only(SimpleNamespace(st_file_attributes=0x00001000))  # offline (older sync clients)
    assert not acquire.cloud_only(SimpleNamespace(st_flags=0, st_file_attributes=0x20))  # a normal file
    assert not acquire.cloud_only(os.stat(__file__))
    if os.name == "nt":
        assert paths.documents_folder().is_dir()  # asks Windows for the Documents folder
    else:
        assert paths.documents_folder() == paths.Path.home() / "Documents"

    monkeypatch.setattr(diskspace.Path, "home", classmethod(lambda cls: tmp_path))
    cs = tmp_path / "Library" / "CloudStorage"
    assert diskspace.cloud_provider(cs / "OneDrive-Personal" / "research brain PDFs") == "OneDrive"
    assert diskspace.cloud_provider(cs / "Dropbox" / "papers") == "Dropbox"
    assert diskspace.cloud_provider(tmp_path / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "x") == "iCloud Drive"
    assert diskspace.cloud_provider(tmp_path / "Documents" / "research brain" / "PDFs") == ""


def test_hash_index_never_reads_an_online_only_pdf(tmp_path, monkeypatch):
    pdfs = tmp_path / "pdfs"
    pdfs.mkdir()
    idx = tmp_path / "index"
    idx.mkdir()
    store = SimpleNamespace(pdf_dir=pdfs, index_dir=idx)
    (store.pdf_dir / "a.pdf").write_bytes(b"%PDF-1.4 first")
    (store.pdf_dir / "b.pdf").write_bytes(b"%PDF-1.4 second")
    first = acquire.PdfHashes(store).refresh()
    assert set(first) == {"a", "b"}

    # both files go online-only, and the sync app gives them a new mtime: nothing is read, old hashes stay
    for p in store.pdf_dir.glob("*.pdf"):
        os.utime(p, ns=(1_000_000_000, 1_000_000_000))
    (store.pdf_dir / "c.pdf").write_bytes(b"%PDF-1.4 third, never on this computer")
    monkeypatch.setattr(acquire, "cloud_only", lambda st: True)
    read = []
    monkeypatch.setattr(acquire, "sha256_file", lambda p: read.append(p) or "x")
    again = acquire.PdfHashes(store).refresh()
    assert read == []
    assert again == first  # c has no hash until it's on this computer

    # downloaded again: hashed normally
    monkeypatch.setattr(acquire, "cloud_only", lambda st: False)
    acquire.PdfHashes(store).refresh()
    assert {p.name for p in read} == {"a.pdf", "b.pdf", "c.pdf"}


def test_a_full_disk_answers_507_keeps_the_note_whole_and_refuses_a_pdf(client, monkeypatch):
    """A disk that fills mid-save answers 507 with disk_full, keeps the note's old text and leaves no temp file;
    with too little room a PDF import is refused with 507 and nothing stored, and the status says the disk is low."""
    from app import server
    nid = client.post("/api/notes", json={"type": "note", "title": "Kept whole", "body": "before"}).json()["id"]
    before = server.store.get(nid)["body"]
    real = Path.write_text

    def full(self, *a, **kw):
        if self.name.endswith(".tmp"):
            real(self, "half", encoding="utf-8")
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, *a, **kw)
    monkeypatch.setattr(Path, "write_text", full)
    r = client.put(f"/api/notes/{nid}", json={"body": "after"})
    monkeypatch.setattr(Path, "write_text", real)
    assert r.status_code == 507 and r.json()["disk_full"], r.text
    assert server.store.get(nid)["body"] == before
    folder = server.store.docs
    assert not [p for p in folder.rglob(".*.tmp")]

    ds = server.diskspace  # the client fixture reloads the app's modules
    monkeypatch.setattr(ds, "free_bytes", lambda p: 100 * ds.MB)
    r = client.post("/api/import/pdf", files={"file": ("a.pdf", make_pdf("A paper on sheaths", "Body."), "application/pdf")})
    assert r.status_code == 507, r.text
    assert "Not enough space" in r.json()["detail"]
    assert list(Path(server.store.pdf_dir).glob("*.pdf")) == []
    st = client.get("/api/status").json()["disk"]
    assert st["low"] and st["free_text"] == "100 MB"


def test_keep_new_pdfs_online_only(client, monkeypatch):
    from app import server
    ds = server.diskspace  # the client fixture reloads the app's modules
    asked = []
    monkeypatch.setattr(ds, "keep_online_only", lambda p, recursive=False: asked.append((Path(p).name, recursive)) or True)
    f = client.get("/api/papers-folder").json()
    assert f["online_only"] is False and "cloud" in f and "disk" in f
    r = client.put("/api/papers-folder/online-only", json={"on": True}).json()
    assert r["online_only"] is True and asked and asked[0][1] is True  # the PDFs already there are freed too
    asked.clear()
    nid = client.post("/api/import/pdf", files={"file": ("b.pdf", make_pdf("Online only paper", "Body."), "application/pdf")}).json()["id"]
    assert asked == [(f"{nid}.pdf", False)]  # the new PDF was handed to the sync app
    # moving the folder keeps the choice
    assert client.get("/api/papers-folder").json()["online_only"] is True
    client.put("/api/papers-folder/online-only", json={"on": False})
    asked.clear()
    client.post("/api/import/pdf", files={"file": ("c.pdf", make_pdf("A kept paper", "Body two."), "application/pdf")})
    assert asked == []
