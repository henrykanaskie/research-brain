"""Running out of disk space, and new PDFs kept online only in a cloud papers folder (app/diskspace.py)."""
import errno
import sqlite3
from pathlib import Path

import pytest

from app import diskspace

from test_api import client, make_pdf  # noqa: F401  (fixture)


def test_room_is_checked_against_what_is_always_left_free(tmp_path, monkeypatch):
    monkeypatch.setattr(diskspace, "free_bytes", lambda p: 500 * diskspace.MB)
    monkeypatch.setattr(diskspace, "MIN_FREE", 300 * diskspace.MB)
    diskspace.ensure_room(tmp_path, 100 * diskspace.MB)
    with pytest.raises(diskspace.DiskFull) as e:
        diskspace.ensure_room(tmp_path, 250 * diskspace.MB)
    assert "500 MB free" in str(e.value) and "OneDrive" in str(e.value)


def test_disk_full_is_recognised_from_the_os_and_from_sqlite():
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


def test_a_failed_copy_leaves_no_partial_file(tmp_path, monkeypatch):
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


def test_cloud_folders_are_recognised(monkeypatch, tmp_path):
    monkeypatch.setattr(diskspace.Path, "home", classmethod(lambda cls: tmp_path))
    cs = tmp_path / "Library" / "CloudStorage"
    assert diskspace.cloud_provider(cs / "OneDrive-Personal" / "research brain PDFs") == "OneDrive"
    assert diskspace.cloud_provider(cs / "Dropbox" / "papers") == "Dropbox"
    assert diskspace.cloud_provider(tmp_path / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "x") == "iCloud Drive"
    assert diskspace.cloud_provider(tmp_path / "Documents" / "research brain" / "PDFs") == ""


def test_adding_a_pdf_is_refused_when_there_is_no_room(client, monkeypatch):
    from app import server
    diskspace = server.diskspace  # the client fixture reloads the app's modules
    monkeypatch.setattr(diskspace, "free_bytes", lambda p: 100 * diskspace.MB)
    r = client.post("/api/import/pdf", files={"file": ("a.pdf", make_pdf("A paper on sheaths", "Body."), "application/pdf")})
    assert r.status_code == 507, r.text
    assert "Not enough space" in r.json()["detail"]
    assert list(Path(server.store.pdf_dir).glob("*.pdf")) == []
    st = client.get("/api/status").json()["disk"]
    assert st["low"] and st["free_text"] == "100 MB"


def test_a_disk_that_fills_mid_save_answers_507_and_keeps_the_note_whole(client, monkeypatch):
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


def test_keep_new_pdfs_online_only(client, monkeypatch):
    from app import server
    diskspace = server.diskspace  # the client fixture reloads the app's modules
    asked = []
    monkeypatch.setattr(diskspace, "keep_online_only", lambda p, recursive=False: asked.append((Path(p).name, recursive)) or True)
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
