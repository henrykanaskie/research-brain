"""The in-process way for tests to get a research_brain server: a freshly imported app.server on a temporary
vault, with the TF-IDF embedder and no background work, plus small helpers to add notes through the API.

    srv = load_server(monkeypatch, tmp_path)                       # the fake Claude on
    srv = load_server(monkeypatch, tmp_path, fake_claude=False,    # no fake Claude, extra settings
                      RB_MEMORY_WATCH="0", RB_AGENT=None)          # None removes a variable

For a real server process (a port, a browser), use rb_testserver.run_server instead.
"""
from __future__ import annotations

import importlib
import sys


def purge_app(prefix: str = "app") -> None:
    """Forget every imported app module, so the next import reads the environment again."""
    for m in [m for m in sys.modules if m == prefix or m.startswith(prefix + ".")]:
        del sys.modules[m]


def load_server(monkeypatch, tmp_path, *, fake_claude: bool = True, vault=None, offline_scholar=False, **env):
    """Import a fresh app.server on tmp_path/'vault' (or `vault`). Each keyword sets an environment variable
    (None removes it). With offline_scholar, paper lookups and citation fetches never touch the network."""
    monkeypatch.setenv("RB_VAULT", str(vault if vault is not None else tmp_path / "vault"))
    monkeypatch.setenv("RB_EMBEDDER", "tfidf")
    if fake_claude:
        monkeypatch.setenv("RB_FAKE_CLAUDE", "1")
    else:
        monkeypatch.delenv("RB_FAKE_CLAUDE", raising=False)
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, str(v))
    purge_app()
    srv = importlib.import_module("app.server")
    if offline_scholar:
        monkeypatch.setattr(srv.scholar, "lookup", lambda q: None)
        monkeypatch.setattr(srv.scholar, "s2_paper", lambda pid: None)
        monkeypatch.setattr(srv.citations, "_sleep", lambda s: None)
    return srv


def add_note(client, **fields) -> str:
    """Create a note through the API (a paper unless `type` says otherwise) and return its id."""
    fields.setdefault("type", "paper")
    r = client.post("/api/notes", json=fields)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def add_paper(client, title, abstract="", **fields) -> str:
    """Create a paper with this title and abstract through the API and return its id."""
    return add_note(client, type="paper", title=title, abstract=abstract, **fields)
