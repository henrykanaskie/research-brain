"""Storage, persistence and distribution: data-folder resolution and the legacy ./vault, profiles, schema
version + migrations with backup, open-access PDF acquisition (mocked downloads), PDF bytes from the
browser extension, whole-library zip export/import (zip-slip), UI state and settings, and the CLI.

No network: urlfetch's DNS and GET, Unpaywall and Semantic Scholar are replaced.
"""
from __future__ import annotations

import importlib
import io
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ATTN = dict(s2_id="s2-attn", title="Attention Is All You Need", year=2017, authors=["Ashish Vaswani"],
            venue="NeurIPS", doi="", arxiv="1706.03762", url="", abstract="The Transformer.", citationCount=1)
HALL = dict(s2_id="s2-hall", title="Hall thruster plasma oscillations", year=2016, authors=["A. Physicist"],
            venue="Physics of Plasmas", doi="10.1063/1.4945558", arxiv="", url="", abstract="Breathing mode.",
            citationCount=3, oa_pdf="https://journals.example.org/oa/hall.pdf")


def make_pdf(title="Sheath physics", line="The Bohm criterion holds.", extra=""):
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.setTitle(title)
    c.drawString(72, 720, title)
    c.drawString(72, 680, line)
    if extra:
        c.drawString(72, 660, extra)
    c.showPage()
    c.save()
    return buf.getvalue()


def fresh(prefix="app"):
    for m in [m for m in sys.modules if m == prefix or m.startswith(prefix + ".")]:
        del sys.modules[m]


@pytest.fixture()
def dirs(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("RB_DATA_DIR", str(data))
    monkeypatch.setenv("RB_LEGACY_VAULT", str(tmp_path / "legacy-vault"))
    monkeypatch.delenv("RB_VAULT", raising=False)
    monkeypatch.delenv("RB_PROFILE", raising=False)
    monkeypatch.delenv("RB_RESTART_FILE", raising=False)
    monkeypatch.setenv("RB_FAKE_CLAUDE", "1")
    monkeypatch.setenv("RB_EMBEDDER", "tfidf")
    monkeypatch.setenv("RB_CITES_ON_IMPORT", "0")
    return tmp_path


def load_server(monkeypatch, vault=None):
    if vault is not None:
        monkeypatch.setenv("RB_VAULT", str(vault))
    fresh()
    server = importlib.import_module("app.server")
    sch = server.scholar

    def lookup(q):
        kind, value = sch.parse_identifier(q)
        if kind == "arxiv" and value == "1706.03762":
            return dict(ATTN)
        if kind == "doi" and value == HALL["doi"]:
            return dict(HALL)
        return None
    monkeypatch.setattr(sch, "lookup", lookup)
    from fastapi.testclient import TestClient
    return TestClient(server.app), server


# ================================================================ data folder + legacy ./vault
def test_user_data_dir_per_platform(monkeypatch, tmp_path):
    from app import paths
    monkeypatch.delenv("RB_DATA_DIR", raising=False)
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(paths.sys, "platform", "darwin")
    assert paths.user_data_dir() == tmp_path / "Library" / "Application Support" / "research_brain"
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert paths.user_data_dir() == tmp_path / "xdg" / "research_brain"
    monkeypatch.delenv("XDG_DATA_HOME")
    assert paths.user_data_dir() == tmp_path / ".local" / "share" / "research_brain"
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert paths.user_data_dir() == tmp_path / "Roaming" / "research_brain"


def test_resolution_order(dirs, monkeypatch):
    from app import paths
    data = dirs / "data"
    r = paths.resolve()
    assert r.vault == data / "profiles" / "default" and r.profile == "default" and not r.pinned
    paths.save_settings({"active_profile": "thesis"})
    assert paths.resolve().vault == data / "profiles" / "thesis" and paths.resolve().source == "settings"
    monkeypatch.setenv("RB_PROFILE", "teaching")
    assert paths.resolve().profile == "teaching"
    assert paths.resolve(profile="other").profile == "other"  # --profile beats RB_PROFILE
    monkeypatch.setenv("RB_VAULT", str(dirs / "pinned"))
    r = paths.resolve()
    assert r.vault == dirs / "pinned" and r.pinned and r.profile == ""
    assert paths.resolve(vault=dirs / "cli").vault == dirs / "cli"
    monkeypatch.delenv("RB_VAULT")
    monkeypatch.setenv("RB_PROFILE", "../etc")
    r = paths.resolve()
    assert r.profile == "default" and any("invalid profile" in n for n in r.notices)
    paths.save_settings({"profile_paths": {"shared": str(dirs / "usb")}})
    assert paths.resolve(profile="shared").vault == dirs / "usb"


def test_legacy_vault_kept_then_moved_safely(dirs, monkeypatch):
    from app import paths
    legacy = dirs / "legacy-vault"
    (legacy / "notes").mkdir(parents=True)
    (legacy / "notes" / "a.md").write_text("---\ntitle: A\n---\n")
    (legacy / "pdfs").mkdir()
    (legacy / "pdfs" / "a.pdf").write_bytes(b"%PDF-1.4 x")
    monkeypatch.setattr(paths, "is_source_checkout", lambda: True)
    r = paths.resolve()
    assert r.legacy and r.vault == legacy and any("move-legacy-vault" in n for n in r.notices)
    # an empty default profile folder (created by an earlier run) doesn't hide the legacy library
    (dirs / "data" / "profiles" / "default" / "notes").mkdir(parents=True)
    assert paths.resolve().legacy
    dst = paths.move_legacy_vault()
    assert dst == dirs / "data" / "profiles" / "default"
    assert (dst / "notes" / "a.md").read_text(encoding="utf-8").startswith("---") and (dst / "pdfs" / "a.pdf").exists()
    assert not legacy.exists() and list(dirs.glob("vault.moved-*"))  # renamed, not deleted
    r = paths.resolve()
    assert not r.legacy and r.vault == dst
    assert "moved legacy vault" in (dirs / "data" / "storage.log").read_text(encoding="utf-8")
    with pytest.raises(paths.MoveError):
        paths.move_legacy_vault()  # nothing left to move


def test_move_refuses_to_overwrite(dirs, monkeypatch):
    from app import paths
    legacy = dirs / "legacy-vault"
    (legacy / "notes").mkdir(parents=True)
    (legacy / "notes" / "a.md").write_text("x")
    dst = dirs / "data" / "profiles" / "default" / "notes"
    dst.mkdir(parents=True)
    (dst / "b.md").write_text("y")
    with pytest.raises(paths.MoveError):
        paths.move_legacy_vault()
    assert (legacy / "notes" / "a.md").exists() and (dst / "b.md").exists()


def test_paths_script_prints_vault(dirs):
    env = dict(os.environ)
    out = subprocess.run([sys.executable, str(ROOT / "app" / "paths.py")], capture_output=True, text=True, env=env)
    assert out.returncode == 0 and out.stdout.strip() == str(dirs / "data" / "profiles" / "default")


# ================================================================ migrations
def test_new_vault_gets_current_schema(tmp_path):
    from app import migrate
    v = tmp_path / "v"
    res = migrate.run(v, log=lambda m: None)
    assert res["backup"] is None and migrate.read_meta(v)["schema"] == migrate.SCHEMA_VERSION
    assert not (v / "backups").exists()


def test_old_vault_is_backed_up_then_migrated(tmp_path):
    from app import migrate
    v = tmp_path / "v"
    for d in ("notes", "pdfs", ".index"):
        (v / d).mkdir(parents=True)
    (v / "notes" / "a.md").write_text("---\ntitle: A\n---\n")
    (v / "pdfs" / "a.pdf").write_bytes(b"%PDF big")
    (v / ".index" / "review.json").write_text('{"a": 1}')
    res = migrate.run(v, log=lambda m: None)
    assert res["from"] == 0 and res["to"] == migrate.SCHEMA_VERSION and res["applied"] == [1, 2]
    assert json.loads((v / "state" / "review.json").read_text(encoding="utf-8")) == {"a": 1}
    assert not (v / ".index" / "review.json").exists()
    names = zipfile.ZipFile(res["backup"]).namelist()
    assert "notes/a.md" in names and not any(n.startswith(("pdfs/", ".index/")) for n in names)
    meta = migrate.read_meta(v)
    assert meta["migrations"][0]["backup"].startswith(f"backups/pre-migration-v0-to-v{migrate.SCHEMA_VERSION}-")
    # v2: readable folders, the file's text kept apart from its id
    assert (v / "Notes" / "A.md").read_text(encoding="utf-8") == "---\nid: a\ntitle: A\n---\n"
    assert (v / "PDFs" / "a.pdf").read_bytes() == b"%PDF big" and "notes" not in os.listdir(v)
    again = migrate.run(v, log=lambda m: None)  # idempotent, no second backup
    assert again["applied"] == [] and len(list((v / "backups").glob("*.zip"))) == 1


def test_newer_schema_refuses_to_start(tmp_path, monkeypatch):
    from app import migrate
    v = tmp_path / "v"
    (v / "state").mkdir(parents=True)
    (v / "state" / "meta.json").write_text(json.dumps({"schema": migrate.SCHEMA_VERSION + 5}))
    with pytest.raises(migrate.SchemaError, match="newer research_brain"):
        migrate.run(v, log=lambda m: None)
    monkeypatch.setenv("RB_VAULT", str(v))
    fresh()
    with pytest.raises(Exception, match="newer research_brain"):
        importlib.import_module("app.server")
    fresh()


def test_migration_steps_are_registered():
    from app import migrate
    assert sorted(migrate.MIGRATIONS) == list(range(1, migrate.SCHEMA_VERSION + 1))


# ================================================================ profiles + API
def test_profiles_api_and_switch(dirs, monkeypatch):
    from app import migrate
    c, server = load_server(monkeypatch)
    lib = c.get("/api/library").json()
    assert lib["profile"] == "default" and not lib["pinned"] and lib["schema"] == migrate.SCHEMA_VERSION
    assert c.post("/api/profiles", json={"name": "Bad Name!"}).status_code == 400
    assert c.post("/api/profiles", json={"name": "thesis"}).status_code == 201
    assert c.post("/api/profiles", json={"name": "thesis"}).status_code == 409
    names = [p["name"] for p in c.get("/api/profiles").json()["profiles"]]
    assert names == ["default", "thesis"]
    assert c.post("/api/profiles/switch", json={"name": "nope"}).status_code == 404
    r = c.post("/api/profiles/switch", json={"name": "thesis"}).json()
    assert r["restart_required"] and not r["restarting"]  # plain uvicorn: applies on next start
    from app import paths
    assert paths.load_settings()["active_profile"] == "thesis"
    # under `research-brain serve` the server asks for a restart instead
    restart = dirs / "restart.json"
    monkeypatch.setenv("RB_RESTART_FILE", str(restart))
    killed = []
    monkeypatch.setattr(server.storage_api, "_terminate", lambda: killed.append(1))
    assert c.post("/api/profiles/switch", json={"name": "default"}).json()["restarting"] is False  # already open
    r = c.post("/api/profiles/switch", json={"name": "thesis"}).json()
    assert r["restarting"] and json.loads(restart.read_text(encoding="utf-8")) == {"profile": "thesis"}
    time.sleep(0.9)
    assert killed == [1]


def test_pinned_vault_cannot_switch(dirs, monkeypatch):
    c, _ = load_server(monkeypatch, vault=dirs / "pinned")
    assert c.get("/api/library").json()["pinned"]
    assert c.post("/api/profiles/switch", json={"name": "default"}).status_code == 409


def test_settings_and_ui_state(dirs, monkeypatch):
    c, _ = load_server(monkeypatch, vault=dirs / "v")
    assert c.get("/api/settings").json() == {"unpaywall_email": "", "auto_open_access": True, "save_mode": "link"}
    assert c.put("/api/settings", json={"unpaywall_email": "not an email"}).status_code == 400
    s = c.put("/api/settings", json={"unpaywall_email": "me@uni.edu", "auto_open_access": False}).json()
    assert s == {"unpaywall_email": "me@uni.edu", "auto_open_access": False, "save_mode": "link"}
    assert c.put("/api/settings", json={"save_mode": "everything"}).status_code == 400
    assert c.put("/api/settings", json={"save_mode": "pdf"}).json()["save_mode"] == "pdf"
    assert c.get("/api/import/linked/prefs").json() == {"save_mode": "pdf"}
    assert c.put("/api/settings", json={"save_mode": "link"}).json()["save_mode"] == "link"
    assert c.put("/api/ui-state", json={"state": {"panelW": 420, "theme": "dark"}}).json()["state"] == {"panelW": 420, "theme": "dark"}
    assert c.put("/api/ui-state", json={"state": {"theme": None, "lastNote": "a"}}).json()["state"] == {"panelW": 420, "lastNote": "a"}
    assert json.loads((dirs / "v" / "state" / "ui.json").read_text(encoding="utf-8")) == {"panelW": 420, "lastNote": "a"}
    assert c.put("/api/ui-state", json={"state": {"../x": 1}}).status_code == 400
    assert c.put("/api/ui-state", json={"state": {"big": "x" * 300_000}}).status_code == 413
    assert c.get("/api/ui-state").json()["state"]["panelW"] == 420


# ================================================================ open-access acquisition
class FakeWeb:
    def __init__(self):
        self.routes, self.calls, self.unpaywall = {}, [], {}

    def install(self, monkeypatch, acquire):
        def download(url, max_bytes):
            self.calls.append(url)
            body = self.routes.get(url)
            if body is None:
                raise acquire.urlfetch.FetchError("The site answered HTTP 404", 502)
            if len(body) > max_bytes:
                raise acquire.urlfetch.FetchError("too big", 413)
            d = Path(__import__("tempfile").mkdtemp(prefix="rb_test_"))
            (d / "download.pdf").write_bytes(body)
            return d / "download.pdf", url
        monkeypatch.setattr(acquire, "_download", download)
        monkeypatch.setattr(acquire, "_unpaywall", lambda doi, email: self.unpaywall.get(doi))


def _wait(fn, timeout=10.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError("timed out")


def test_add_by_arxiv_fetches_open_access_pdf_in_background(dirs, monkeypatch):
    monkeypatch.setenv("RB_ACQUIRE", "1")
    c, server = load_server(monkeypatch, vault=dirs / "v")
    web = FakeWeb()
    web.install(monkeypatch, server.storage_api.acquire)
    web.routes["https://arxiv.org/pdf/1706.03762"] = make_pdf("Attention Is All You Need", "Transformers attend.")
    r = c.post("/api/import/lookup", json={"query": "1706.03762"})
    assert r.status_code == 201 and r.json()["has_pdf"] is False  # returned before the download
    nid = r.json()["id"]
    st = _wait(lambda: (lambda s: s if s["state"] == "found" else None)(c.get(f"/api/notes/{nid}/acquire").json()))
    assert st["source"] == "arxiv" and st["has_pdf"] and len(st["sha256"]) == 64
    note = c.get(f"/api/notes/{nid}").json()
    assert note["has_pdf"] and "Transformers attend" in (dirs / "v" / "text" / f"{nid}.txt").read_text(encoding="utf-8")
    assert json.loads((dirs / "v" / "state" / "acquire.json").read_text(encoding="utf-8"))[nid]["state"] == "found"


def test_semantic_scholar_then_unpaywall_and_metadata_only_fallback(dirs, monkeypatch):
    fresh()
    from app import acquire
    from app.store import Store
    store = Store(dirs / "v")
    web = FakeWeb()
    web.install(monkeypatch, acquire)
    monkeypatch.setattr(acquire, "extract", lambda p: ("text of the pdf", ""))
    n = store.create({"type": "paper", "title": "Hall", "doi": HALL["doi"]})
    # no email: Unpaywall isn't asked; the S2 link 404s -> metadata-only, recorded with a hint
    rec = acquire.acquire(store, n["id"], dict(HALL))
    assert rec["state"] == "not-found" and rec["tried"][0]["source"] == "semantic-scholar"
    assert not store.get(n["id"])["has_pdf"]
    # with an email, Unpaywall's best location is tried after S2
    web.unpaywall[HALL["doi"]] = {"best_oa_location": {"url_for_pdf": "https://repo.example.edu/hall.pdf"},
                                  "oa_locations": [{"url_for_pdf": "https://repo.example.edu/hall.pdf"}]}
    web.routes["https://repo.example.edu/hall.pdf"] = make_pdf("Hall")
    rec = acquire.acquire(store, n["id"], dict(HALL), email="me@uni.edu")
    assert rec["state"] == "found" and rec["source"] == "unpaywall"
    assert web.calls[-2:] == ["https://journals.example.org/oa/hall.pdf", "https://repo.example.edu/hall.pdf"]
    assert store.get(n["id"])["has_pdf"]
    assert acquire.acquire(store, n["id"], dict(HALL))["state"] == "has-pdf"  # never replaces a PDF
    # nothing to try at all
    m = store.create({"type": "paper", "title": "No ids"})
    rec = acquire.acquire(store, m["id"], {})
    assert rec["state"] == "not-found" and "contact email" in rec["message"]
    # the same bytes for a second note: not stored twice
    d = store.create({"type": "paper", "title": "Hall again", "doi": "10.1/other"})
    web.unpaywall["10.1/other"] = {"best_oa_location": {"url_for_pdf": "https://repo.example.edu/hall.pdf"}}
    rec = acquire.acquire(store, d["id"], {}, email="me@uni.edu")
    assert rec["state"] == "duplicate" and rec["same_as"] == n["id"] and not store.get(d["id"])["has_pdf"]


def test_acquisition_uses_the_ssrf_guard(dirs, monkeypatch):
    fresh()
    from app import acquire, urlfetch
    from app.store import Store
    store = Store(dirs / "v")
    monkeypatch.setattr(urlfetch, "resolve", lambda host: ["10.0.0.5"])  # a private address
    opened = []
    monkeypatch.setattr(urlfetch, "_open", lambda url: opened.append(url))
    n = store.create({"type": "paper", "title": "X", "arxiv": "2401.00001"})
    rec = acquire.acquire(store, n["id"], {"oa_pdf": "http://169.254.169.254/latest/meta-data"})
    assert rec["state"] == "not-found" and not opened
    assert all("private" in t["error"].lower() or "local" in t["error"].lower() for t in rec["tried"])


def test_acquire_disabled_and_settings_off(dirs, monkeypatch):
    c, server = load_server(monkeypatch, vault=dirs / "v")  # conftest: RB_ACQUIRE=0
    r = c.post("/api/import/lookup", json={"query": "1706.03762"}).json()
    assert c.get(f"/api/notes/{r['id']}/acquire").json()["state"] == "none"
    assert c.post(f"/api/notes/{r['id']}/acquire").status_code == 503
    monkeypatch.setenv("RB_ACQUIRE", "1")
    c.put("/api/settings", json={"auto_open_access": False})
    assert server.storage_api.on_paper_added(r["id"], {}) is False


# ================================================================ PDF bytes from the extension
def test_pdf_bytes_upload(dirs, monkeypatch):
    c, server = load_server(monkeypatch, vault=dirs / "v")
    pdf = make_pdf("Attention Is All You Need", "Paywalled copy")
    url = "/api/import/pdf-bytes?source_url=https%3A%2F%2Farxiv.org%2Fpdf%2F1706.03762v7&title=Attention"
    assert c.post(url, content=pdf, headers={"Content-Type": "application/json"}).status_code == 415
    assert c.post(url, content=b"<html>sign in</html>", headers={"Content-Type": "application/pdf"}).status_code == 415
    assert c.post(url, content=b"", headers={"Content-Type": "application/pdf"}).status_code == 400
    r = c.post(url, content=pdf, headers={"Content-Type": "application/pdf"})
    assert r.status_code == 201, r.text
    n = r.json()
    assert n["title"] == "Attention Is All You Need" and n["arxiv"] == "1706.03762" and n["has_pdf"]
    assert n["viewer"] == f"/viewer/{n['id']}"
    again = c.post(url, content=pdf, headers={"Content-Type": "application/pdf"}).json()  # same sha256
    assert again["id"] == n["id"] and again["duplicate"] and again["existing"]
    assert len(list(server.store.pdf_dir.glob("*.pdf"))) == 1


def test_pdf_bytes_attach_to_metadata_only_paper_by_doi(dirs, monkeypatch):
    c, server = load_server(monkeypatch, vault=dirs / "v")
    meta = c.post("/api/import/lookup", json={"query": HALL["doi"]}).json()
    assert not meta["has_pdf"]
    r = c.post(f"/api/import/pdf-bytes?doi={HALL['doi']}&source_url=https://pubs.example.org/doi/pdf/x",
               content=make_pdf("Hall thruster plasma oscillations"), headers={"Content-Type": "application/pdf"})
    assert r.status_code == 201
    assert r.json()["id"] == meta["id"] and r.json()["has_pdf"] and r.json()["attached"]


def test_pdf_bytes_limits_and_origin(dirs, monkeypatch):
    c, server = load_server(monkeypatch, vault=dirs / "v")
    monkeypatch.setattr(server.storage_api, "UPLOAD_MAX_MB", 0)  # 0 MB: everything is too large
    h = {"Content-Type": "application/pdf"}
    assert c.post("/api/import/pdf-bytes", content=make_pdf(), headers=h).status_code == 413
    monkeypatch.setattr(server.storage_api, "UPLOAD_MAX_MB", 150)
    bad = c.post("/api/import/pdf-bytes", content=make_pdf(), headers={**h, "Origin": "https://evil.example"})
    assert bad.status_code == 403
    ok = c.post("/api/import/pdf-bytes?source_url=file:///etc/passwd", content=make_pdf("Plain"),
                headers={**h, "Origin": f"chrome-extension://{server.security._manifest_extension_id()}"})
    assert ok.status_code == 201 and ok.json()["url"] == ""  # a non-http source URL is dropped


# ================================================================ vault zip
def _vault(v: Path):
    for d in ("Notes", "PDFs", "text", "state", "memory", ".index", ".trash", "exports/x", "backups"):
        (v / d).mkdir(parents=True, exist_ok=True)
    (v / "Notes" / "a.md").write_text("---\nid: a\ntitle: A\n---\nbody\n")
    (v / "PDFs" / "a.pdf").write_bytes(b"%PDF-1.4 a")
    (v / "text" / "a.txt").write_text("text")
    (v / "state" / "review.json").write_text("{}")
    (v / "memory" / "findings.md").write_text("f")
    (v / ".index" / "emb.npy").write_bytes(b"x")
    (v / "exports" / "x" / "index.html").write_text("<html>")
    (v / "backups" / "old.zip").write_bytes(b"PK")


def test_zip_round_trip(tmp_path):
    from app import vaultzip
    v = tmp_path / "v"
    _vault(v)
    m = vaultzip.export_zip(v, tmp_path / "lib.zip")
    names = set(zipfile.ZipFile(tmp_path / "lib.zip").namelist())
    assert names == {"Notes/a.md", "PDFs/a.pdf", "text/a.txt", "state/review.json", "memory/findings.md",
                     vaultzip.MANIFEST}
    assert m["notes"] == 1 and m["pdfs"] == 1
    vaultzip.export_zip(v, tmp_path / "np.zip", include_pdfs=False)
    assert "PDFs/a.pdf" not in zipfile.ZipFile(tmp_path / "np.zip").namelist()
    res = vaultzip.import_zip(tmp_path / "lib.zip", tmp_path / "new")
    assert res["notes"] == 1 and (tmp_path / "new" / "PDFs" / "a.pdf").read_bytes() == b"%PDF-1.4 a"
    assert res["manifest"]["format"] == "research_brain-vault"
    with pytest.raises(vaultzip.ZipImportError, match="isn't empty"):
        vaultzip.import_zip(tmp_path / "lib.zip", tmp_path / "new")


def _zip(path, entries, symlink=None):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in entries:
            z.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.create_system = 3
            info.external_attr = (0o120777 << 16)
            z.writestr(info, "/etc/passwd")
    return path


@pytest.mark.parametrize("bad", ["../evil.md", "notes/../../evil.md", "/abs/notes/a.md", "C:/x/notes/a.md",
                                 "notes\\..\\..\\evil.md"])
def test_zip_slip_is_refused(tmp_path, bad):
    from app import vaultzip
    z = _zip(tmp_path / "bad.zip", [("notes/a.md", "x"), (bad, "pwned")])
    with pytest.raises(vaultzip.ZipImportError):
        vaultzip.import_zip(z, tmp_path / "t")
    assert not (tmp_path / "evil.md").exists() and not (tmp_path / "t").exists()


def test_zip_symlink_unknown_dirs_wrapper_and_bomb(tmp_path, monkeypatch):
    from app import vaultzip
    with pytest.raises(vaultzip.ZipImportError, match="symbolic link"):
        vaultzip.import_zip(_zip(tmp_path / "s.zip", [("notes/a.md", "x")], symlink="notes/link.md"), tmp_path / "t1")
    with pytest.raises(vaultzip.ZipImportError, match="no papers, notes or questions"):
        vaultzip.import_zip(_zip(tmp_path / "e.zip", [("random/a.md", "x")]), tmp_path / "t2")
    res = vaultzip.import_zip(_zip(tmp_path / "w.zip", [("vault/notes/a.md", "x"), ("vault/.index/e", "y"),
                                                          ("vault/app/server.py", "z")]), tmp_path / "t3")
    assert (tmp_path / "t3" / "notes" / "a.md").exists() and res["skipped_count"] == 2
    assert not (tmp_path / "t3" / "app").exists()
    with zipfile.ZipFile(tmp_path / "bomb.zip", "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("notes/a.md", "x")
        z.writestr("pdfs/big.pdf", b"\0" * (5 << 20))
    with pytest.raises(vaultzip.ZipImportError, match="zip bomb"):
        vaultzip.import_zip(tmp_path / "bomb.zip", tmp_path / "t4")


def test_zip_api_export_and_import_into_new_profile(dirs, monkeypatch):
    c, server = load_server(monkeypatch, vault=dirs / "v")
    c.post("/api/notes", json={"title": "My idea", "type": "note"})
    r = c.get("/api/vault/export?pdfs=false")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert "Notes/My idea.md" in z.namelist() and "state/meta.json" in z.namelist()
    h = {"Content-Type": "application/zip"}
    assert c.post("/api/vault/import?profile=Bad!", content=r.content, headers=h).status_code == 400
    assert c.post("/api/vault/import?profile=copy", content=b"not a zip", headers=h).status_code == 415
    res = c.post("/api/vault/import?profile=copy", content=r.content, headers=h)
    assert res.status_code == 201, res.text
    assert res.json()["notes"] == 1 and res.json()["profile"] == "copy"
    assert (dirs / "data" / "profiles" / "copy" / "state" / "meta.json").exists()
    assert c.post("/api/vault/import?profile=copy", content=r.content, headers=h).status_code == 409
    assert "copy" in [p["name"] for p in c.get("/api/profiles").json()["profiles"]]


# ================================================================ CLI
def _cli(*args, env=None):
    return subprocess.run([sys.executable, "-m", "app.cli", *args], capture_output=True, text=True, cwd=str(ROOT),
                          env=dict(os.environ, **(env or {})), timeout=60)


def test_cli_version_where_profiles_export_import(dirs):
    from app import __version__
    r = _cli("--version")
    assert r.returncode == 0 and r.stdout.strip() == f"research_brain {__version__}"
    w = json.loads(_cli("where", "--profile", "thesis").stdout)
    assert w["vault"] == str(dirs / "data" / "profiles" / "thesis") and w["extension"].endswith("extension")
    assert _cli("profiles", "create", "thesis").returncode == 0
    assert _cli("profiles", "create", "thesis").returncode == 1
    assert "thesis" in _cli("profiles").stdout
    assert _cli("profiles", "use", "thesis").returncode == 0
    assert json.loads(_cli("where").stdout)["profile"] == "thesis"
    (dirs / "data" / "profiles" / "thesis" / "Notes" / "a.md").write_text("---\ntitle: A\n---\n")
    assert _cli("export", str(dirs / "t.zip")).returncode == 0
    r = _cli("import", str(dirs / "t.zip"), "--profile", "laptop2")
    assert r.returncode == 0, r.stderr
    assert (dirs / "data" / "profiles" / "laptop2" / "Notes" / "a.md").exists()
    assert _cli("import", str(dirs / "t.zip"), "--profile", "laptop2").returncode == 1


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p if p != 8795 else _free_port()


def _get(url, data=None):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"} if data is not None else {})
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.load(r)


@pytest.mark.timeout(120)
def test_serve_supervisor_restarts_into_the_selected_profile(dirs):
    port = _free_port()
    env = dict(os.environ, RB_FAKE_CLAUDE="1", RB_EMBEDDER="tfidf", RB_NO_OPEN="1",
               HTTP_PROXY="http://127.0.0.1:9", HTTPS_PROXY="http://127.0.0.1:9", NO_PROXY="127.0.0.1,localhost")
    env.pop("RB_VAULT", None)
    log = open(dirs / "serve.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "app.cli", "serve", "--port", str(port), "--no-open"],
                            cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        lib = _wait(lambda: _try(lambda: _get(base + "/api/library")), 45)
        assert lib["profile"] == "default" and lib["supervised"]
        _get(base + "/api/profiles", {"name": "thesis"})
        r = _get(base + "/api/profiles/switch", {"name": "thesis"})
        assert r["restarting"]
        lib = _wait(lambda: _try(lambda: (lambda x: x if x["profile"] == "thesis" else None)(_get(base + "/api/library"))), 45)
        assert lib["vault"] == str(dirs / "data" / "profiles" / "thesis")
    finally:
        proc.terminate()
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
    assert proc.returncode is not None


def _try(fn):
    try:
        return fn()
    except OSError:
        return None


# ================================================================ extension: Save PDF stays local and AI-free
EXT = ROOT / "extension"


def test_extension_save_pdf_action():
    html = (EXT / "clip.html").read_text(encoding="utf-8")
    js = (EXT / "clip.js").read_text(encoding="utf-8")
    grab = (EXT / "grab_pdf.js").read_text(encoding="utf-8")
    assert "Save PDF to research_brain" in html and 'id="savePdfBtn"' in html
    assert "No AI in this extension. Everything you save goes only to research_brain on this computer." in html
    assert "/api/import/pdf-bytes" in js and '"Content-Type": "application/pdf"' in js
    assert 'files: ["grab_pdf.js"]' in js
    # the page-side script fetches only the one PDF URL it is given, with the browser's own session
    import re
    assert re.findall(r"\bfetch\(([^,]+),", grab) == ["target.href"]
    assert 'credentials: "include"' in grab and "No AI in this extension." in grab
    assert not re.search(r"XMLHttpRequest|WebSocket|sendBeacon|chrome\.runtime\.sendMessage", grab)
    m = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    assert "grab_pdf.js" not in json.dumps(m.get("web_accessible_resources", []))


def test_zip_includes_photo_attachments(tmp_path):
    """Photos captured on a phone live in vault/attachments/ and must survive a backup round trip."""
    from app import vaultzip
    v = tmp_path / "v"
    _vault(v)
    (v / "attachments").mkdir()
    (v / "attachments" / "x1.jpg").write_bytes(b"\xff\xd8\xff\xe0 jpeg")
    vaultzip.export_zip(v, tmp_path / "lib.zip")
    assert "attachments/x1.jpg" in zipfile.ZipFile(tmp_path / "lib.zip").namelist()
    vaultzip.import_zip(tmp_path / "lib.zip", tmp_path / "new")
    assert (tmp_path / "new" / "attachments" / "x1.jpg").read_bytes().startswith(b"\xff\xd8")
