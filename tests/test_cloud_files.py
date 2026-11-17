"""A papers folder in OneDrive or iCloud Drive with its space freed up: online-only PDFs are never read in the
background (reading one downloads it), and on Windows the documents folder follows OneDrive's Documents."""
import os
from types import SimpleNamespace

import pytest

from app import acquire, paths


def test_online_only_on_mac_and_windows():
    assert acquire.cloud_only(SimpleNamespace(st_flags=0x40000000))            # macOS SF_DATALESS
    assert acquire.cloud_only(SimpleNamespace(st_file_attributes=0x00400000))  # OneDrive Files On-Demand
    assert acquire.cloud_only(SimpleNamespace(st_file_attributes=0x00001000))  # offline (older sync clients)
    assert not acquire.cloud_only(SimpleNamespace(st_flags=0, st_file_attributes=0x20))  # a normal file
    assert not acquire.cloud_only(os.stat(__file__))


def _store(tmp_path):
    pdfs = tmp_path / "pdfs"
    pdfs.mkdir()
    idx = tmp_path / "index"
    idx.mkdir()
    return SimpleNamespace(pdf_dir=pdfs, index_dir=idx)


def test_hash_index_never_reads_an_online_only_pdf(tmp_path, monkeypatch):
    store = _store(tmp_path)
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


@pytest.mark.skipif(os.name != "nt", reason="asks Windows for the Documents folder")
def test_windows_documents_folder_is_the_real_one():
    assert paths.documents_folder().is_dir()


def test_documents_folder_elsewhere_is_home_documents():
    if os.name != "nt":
        assert paths.documents_folder() == paths.Path.home() / "Documents"
