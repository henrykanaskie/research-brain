"""Node importance inputs: opens counter + mass in /api/graph."""
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_opens_and_mass(tmp_path, monkeypatch):
    monkeypatch.setenv("RB_VAULT", str(tmp_path / "v"))
    monkeypatch.setenv("RB_FAKE_CLAUDE", "1")
    monkeypatch.setenv("RB_EMBEDDER", "tfidf")
    for m in [m for m in sys.modules if m.startswith("app")]:
        del sys.modules[m]
    server = importlib.import_module("app.server")
    from fastapi.testclient import TestClient
    c = TestClient(server.app)
    a = c.post("/api/notes", json={"type": "paper", "title": "Studied", "summary": "word " * 120}).json()
    b = c.post("/api/notes", json={"type": "paper", "title": "Filed"}).json()
    before = c.get(f"/api/notes/{a['id']}").json()["updated"]
    for _ in range(3):
        assert c.post(f"/api/notes/{a['id']}/opened").status_code == 200
    assert c.get(f"/api/notes/{a['id']}").json()["updated"] == before  # visiting is not editing
    nodes = {n["id"]: n for n in c.get("/api/graph").json()["nodes"]}
    assert nodes[a["id"]]["opens"] == 3 and nodes[b["id"]]["opens"] == 0
    assert nodes[a["id"]]["mass"] > nodes[b["id"]]["mass"]
    assert c.post("/api/notes/no-such-note/opened").status_code == 404


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("RB_VAULT", str(tmp_path / "v"))
    monkeypatch.setenv("RB_FAKE_CLAUDE", "1")
    monkeypatch.setenv("RB_EMBEDDER", "tfidf")
    for m in [m for m in sys.modules if m.startswith("app")]:
        del sys.modules[m]
    server = importlib.import_module("app.server")
    from fastapi.testclient import TestClient
    return server, TestClient(server.app)


def test_graph_cache_sees_new_opens(tmp_path, monkeypatch):
    # regression: /api/graph bytes were cached by the note index signature only, so opens counted
    # after the first graph fetch never showed up (and the page's next refresh shrank the node back)
    _, c = _client(tmp_path, monkeypatch)
    a = c.post("/api/notes", json={"type": "note", "title": "Visited"}).json()
    opens = lambda: {n["id"]: n["opens"] for n in c.get("/api/graph").json()["nodes"]}[a["id"]]
    assert opens() == 0
    c.post(f"/api/notes/{a['id']}/opened")
    c.post(f"/api/notes/{a['id']}/opened")
    assert opens() == 2


def test_unparseable_frontmatter_is_not_overwritten(tmp_path, monkeypatch):
    # regression: a hand-edited note whose YAML fails to parse was read as an empty note, and the next
    # save from the app (autosave, a link, a rename) rewrote the file with blank properties
    server, c = _client(tmp_path, monkeypatch)
    a = c.post("/api/notes", json={"type": "paper", "title": "Real title", "summary": "fine"}).json()
    b = c.post("/api/notes", json={"type": "note", "title": "Other"}).json()
    c.post(f"/api/notes/{a['id']}/link", json={"target": b["id"]})
    p = server.store.note_path(a["id"])
    broken = p.read_text().replace("summary: fine", "summary: oops: a colon")
    p.write_text(broken)
    r = c.put(f"/api/notes/{a['id']}", json={"body": "typed in the app"})
    assert r.status_code == 409 and "frontmatter" in r.json()["detail"]
    c.delete(f"/api/notes/{b['id']}")  # would rewrite a's links list
    assert p.read_text() == broken


def test_delete_forgets_opens(tmp_path, monkeypatch):
    # regression: engagement.json kept deleted ids, so a new note that reused the slug started "visited"
    _, c = _client(tmp_path, monkeypatch)
    a = c.post("/api/notes", json={"type": "note", "title": "Reused"}).json()
    for _ in range(4):
        c.post(f"/api/notes/{a['id']}/opened")
    assert c.delete(f"/api/notes/{a['id']}").status_code == 200
    b = c.post("/api/notes", json={"type": "note", "title": "Reused"}).json()
    assert b["id"] == a["id"]
    assert {n["id"]: n["opens"] for n in c.get("/api/graph").json()["nodes"]}[b["id"]] == 0


def test_state_files_live_outside_index_and_migrate(tmp_path):
    from app.store import Store
    s = Store(tmp_path)
    (s.index_dir / "review.json").write_text('{"items": {"x": 1}}')  # older layout
    assert s.load_json("review.json") == {"items": {"x": 1}}
    assert (tmp_path / "state" / "review.json").exists() and not (s.index_dir / "review.json").exists()
    s.save_json("engagement.json", {"a": 2})
    assert (tmp_path / "state" / "engagement.json").exists()
    s.save_json("explanations.json", {"k": 1})
    assert (s.index_dir / "explanations.json").exists()
