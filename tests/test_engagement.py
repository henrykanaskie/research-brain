"""Node importance inputs (the opens counter and mass in /api/graph) and the note files they sit beside."""
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rb_app import load_server  # noqa: E402


def _client(tmp_path, monkeypatch):
    server = load_server(monkeypatch, tmp_path, vault=tmp_path / "v")
    return server, TestClient(server.app)


def test_opens_count_add_mass_show_after_a_cached_graph_and_are_forgotten_on_delete(tmp_path, monkeypatch):
    """Opening a note counts without editing it, gives it more mass, shows in a graph fetched before the opens
    (regression: the graph bytes were cached by the note index signature only, so later opens never showed up),
    and a deleted note's opens are forgotten (regression: a new note reusing the slug started "visited")."""
    _, c = _client(tmp_path, monkeypatch)
    a = c.post("/api/notes", json={"type": "paper", "title": "Studied", "summary": "word " * 120}).json()
    b = c.post("/api/notes", json={"type": "paper", "title": "Filed"}).json()
    opens = lambda: {n["id"]: n["opens"] for n in c.get("/api/graph").json()["nodes"]}  # noqa: E731
    assert opens()[a["id"]] == 0  # the graph is fetched (and cached) before any open
    before = c.get(f"/api/notes/{a['id']}").json()["updated"]
    for _ in range(3):
        assert c.post(f"/api/notes/{a['id']}/opened").status_code == 200
    assert c.get(f"/api/notes/{a['id']}").json()["updated"] == before  # visiting is not editing
    nodes = {n["id"]: n for n in c.get("/api/graph").json()["nodes"]}
    assert nodes[a["id"]]["opens"] == 3 and nodes[b["id"]]["opens"] == 0
    assert nodes[a["id"]]["mass"] > nodes[b["id"]]["mass"]
    assert c.post("/api/notes/no-such-note/opened").status_code == 404
    # deleting forgets the opens: a new note that reuses the slug starts unvisited
    assert c.delete(f"/api/notes/{a['id']}").status_code == 200
    again = c.post("/api/notes", json={"type": "paper", "title": "Studied"}).json()
    assert again["id"] == a["id"]
    assert opens()[again["id"]] == 0


def test_unparseable_frontmatter_is_not_overwritten(tmp_path, monkeypatch):
    # regression: a hand-edited note whose YAML fails to parse was read as an empty note, and the next
    # save from the app (autosave, a link, a rename) rewrote the file with blank properties
    server, c = _client(tmp_path, monkeypatch)
    a = c.post("/api/notes", json={"type": "paper", "title": "Real title", "summary": "fine"}).json()
    b = c.post("/api/notes", json={"type": "note", "title": "Other"}).json()
    c.post(f"/api/notes/{a['id']}/link", json={"target": b["id"]})
    p = server.store.note_path(a["id"])
    broken = p.read_text(encoding="utf-8").replace("summary: fine", "summary: oops: a colon")
    p.write_text(broken)
    r = c.put(f"/api/notes/{a['id']}", json={"body": "typed in the app"})
    assert r.status_code == 409 and "frontmatter" in r.json()["detail"]
    c.delete(f"/api/notes/{b['id']}")  # would rewrite a's links list
    assert p.read_text(encoding="utf-8") == broken


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
