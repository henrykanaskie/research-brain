"""Scale + metadata-index tests: 2,000 synthetic notes and 300 fragments in a temp vault.

Run:  .venv/bin/python -m pytest -q tests/test_scale.py
Latency budgets (warm, measured on the dev machine): GET /api/graph < 150 ms, GET /api/search < 30 ms.
Set RB_SCALE_SLACK=2 to loosen them on a slow CI box.
"""
import importlib
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from scale_data import write_library  # noqa: E402

SLACK = float(os.environ.get("RB_SCALE_SLACK", "1"))


def _load(vault: Path, monkeypatch):
    monkeypatch.setenv("RB_VAULT", str(vault))
    monkeypatch.setenv("RB_FAKE_CLAUDE", "1")
    monkeypatch.setenv("RB_EMBEDDER", "tfidf")
    for m in [m for m in sys.modules if m.startswith("app")]:
        del sys.modules[m]
    server = importlib.import_module("app.server")
    from fastapi.testclient import TestClient
    return TestClient(server.app), server


@pytest.fixture(scope="module")
def big_vault(tmp_path_factory):
    vault = tmp_path_factory.mktemp("scale") / "vault"
    write_library(vault, n_notes=2000, n_fragments=300)
    return vault


@pytest.fixture()
def small(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    write_library(vault, n_notes=40, n_fragments=6)
    return _load(vault, monkeypatch)


def _best_ms(client, path, n=5, **kw):
    best = 1e9
    for _ in range(n):
        t = time.perf_counter()
        r = client.get(path, **kw)
        best = min(best, (time.perf_counter() - t) * 1000)
        assert r.status_code == 200, r.text
    return best


def test_latency_at_2000_notes(big_vault, monkeypatch):
    client, server = _load(big_vault, monkeypatch)
    g = client.get("/api/graph").json()  # cold: builds the index
    assert len(g["nodes"]) == 2300 and sum(1 for l in g["links"] if l["kind"] == "part") == 300
    graph_ms = _best_ms(client, "/api/graph", n=3)
    search_ms = max(_best_ms(client, "/api/search", params={"q": q}) for q in ("plasma", "thrus", "attention study"))
    empty_ms = _best_ms(client, "/api/search")
    note_ms = _best_ms(client, "/api/notes/syn-00010")
    print(f"\n[scale] graph {graph_ms:.1f} ms, search {search_ms:.1f} ms, recent {empty_ms:.1f} ms, note {note_ms:.1f} ms")
    assert graph_ms < 150 * SLACK, f"warm /api/graph took {graph_ms:.0f} ms"
    assert search_ms < 30 * SLACK, f"/api/search took {search_ms:.0f} ms"
    assert empty_ms < 30 * SLACK


def test_search_ranking_and_shape(small):
    client, server = small
    s = server.store
    a = s.create({"type": "paper", "title": "Breathing mode oscillations", "tags": ["magnetohydro"], "authors": ["Q. Writer"]})
    b = s.create({"type": "note", "title": "Unrelated title", "body": "talks about breathing in the body text"})
    f = s.create({"type": "fragment", "parent": a["id"], "quote": "the breathing frequency scales", "title": "Frag"})
    res = client.get("/api/search", params={"q": "breath"}).json()  # prefix match
    ids = [r["id"] for r in res]
    assert ids[0] == a["id"] and b["id"] in ids and f["id"] not in ids  # title beats body; fragments excluded
    assert set(res[0]) == {"id", "title", "type", "year"}
    withf = [r["id"] for r in client.get("/api/search", params={"q": "breathing frequency", "fragments": 1}).json()]
    assert f["id"] in withf
    assert [r["id"] for r in client.get("/api/search", params={"q": "writer"}).json()] == [a["id"]]  # authors
    assert client.get("/api/search", params={"q": "magnetoh"}).json()[0]["id"] == a["id"]  # tags
    assert client.get("/api/search", params={"q": "\"(*"}).status_code == 200  # FTS syntax can't break it
    assert client.get("/api/search").json()[0]["id"] in (a["id"], b["id"])  # empty q: most recently updated


def test_external_edit_is_reflected(small):
    client, server = small
    p = server.store.notes_dir / "syn-00003.md"
    p.write_text(p.read_text(encoding="utf-8").replace("study 3", "study 3 zanzibarian"), encoding="utf-8")
    assert [r["id"] for r in client.get("/api/search", params={"q": "zanzibar"}).json()] == ["syn-00003"]
    assert "zanzibarian" in client.get("/api/notes/syn-00003").json()["title"]
    assert any("zanzibarian" in n["title"] for n in client.get("/api/graph").json()["nodes"])
    # a brand-new file dropped into notes/ and one removed outside the app
    (server.store.notes_dir / "hand-made.md").write_text("---\ntitle: Quixotic hand note\n---\nbody\n", encoding="utf-8")
    (server.store.notes_dir / "syn-00004.md").unlink()
    ids = {n["id"] for n in server.store.all()}
    assert "hand-made" in ids and "syn-00004" not in ids
    assert client.get("/api/search", params={"q": "quixotic"}).json()[0]["id"] == "hand-made"


def test_corrupt_db_is_rebuilt(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    write_library(vault, n_notes=30, n_fragments=3)
    client, server = _load(vault, monkeypatch)
    assert len(server.store.all()) == 33
    db = vault / ".index" / "meta.sqlite"
    assert db.exists()
    server.store.meta._close()
    for suffix in ("-wal", "-shm"):
        Path(str(db) + suffix).unlink(missing_ok=True)
    db.write_bytes(b"this is not a sqlite database" * 100)
    client, server = _load(vault, monkeypatch)  # restart on the corrupt file
    assert server.store.meta.ok and server.store.meta.rebuilds >= 1
    assert len(server.store.all()) == 33
    assert client.get("/api/search", params={"q": "study"}).status_code == 200
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT count(*) FROM notes").fetchone()[0] == 33
    # deleted while running: the next query rebuilds instead of failing
    server.store.meta.conn.close()
    server.store.meta.conn = sqlite3.connect(":memory:", check_same_thread=False)  # tables gone
    assert len(client.get("/api/search", params={"q": "study"}).json()) > 0


def test_delete_removes_from_fts(small):
    client, server = small
    n = server.store.create({"type": "note", "title": "Perihelion precession notes", "body": "mercury orbit"})
    f = server.store.create({"type": "fragment", "parent": n["id"], "quote": "precession of the perihelion"})
    assert client.get("/api/search", params={"q": "perihelion", "fragments": 1}).json()
    assert client.delete(f"/api/notes/{n['id']}").json() == {"ok": True}
    assert client.get("/api/search", params={"q": "perihelion", "fragments": 1}).json() == []
    with sqlite3.connect(server.store.index_dir / "meta.sqlite") as c:
        assert c.execute("SELECT count(*) FROM notes_fts WHERE notes_fts MATCH 'perihelion'").fetchone()[0] == 0


def test_falls_back_to_files_without_sqlite(small, monkeypatch):
    client, server = small
    server.store.meta.ok = False  # e.g. SQLite unusable on this machine
    assert len(server.store.all()) == 46
    assert client.get("/api/search", params={"q": "study"}).status_code == 200
    assert server.store.find_duplicate(doi="10.1000/syn.1")["id"] == "syn-00001"
