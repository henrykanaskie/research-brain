"""HTTP endpoints for storage, profiles, settings, open-access PDFs, browser PDF uploads and vault zips.

Kept out of app/server.py on purpose: server.py calls install(app, ...) once and on_paper_added() after a
DOI/arXiv/title import. Endpoints (see API.md "Library, profiles and storage"):

    GET  /api/library                      version, schema, vault, profile, notices
    GET  /api/profiles                     list           POST /api/profiles {name}            create
    POST /api/profiles/switch {name}       select + restart (when supervised by `research-brain serve`)
    POST /api/library/move-legacy          move ./vault to the data folder on the next restart
    GET  /api/settings  PUT /api/settings  app-wide preferences (Unpaywall contact email, auto open access)
    GET  /api/ui-state  PUT /api/ui-state  UI state saved in the vault (state/ui.json), not the browser
    GET  /api/notes/{id}/acquire           open-access PDF search status
    POST /api/notes/{id}/acquire           search now
    POST /api/import/pdf-bytes             raw PDF bytes from the browser extension (application/pdf);
                                           ?note=<id> attaches to that paper, ?keep_pdf=0 keeps only its text
    POST /api/import/linked                a "linked paper" from the extension: article URL + metadata + full text
    GET  /api/import/linked/prefs          {save_mode}: what the extension's primary "Save" button does
    GET  /api/vault/export                 the whole library as a zip
    POST /api/vault/import?profile=NAME    a library zip (application/zip) into a NEW profile
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import signal
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from . import __version__, acquire, metadata, migrate, paths, profiles, scholar, vaultzip

UPLOAD_MAX_MB = int(os.environ.get("RB_UPLOAD_MAX_MB", "150"))
UI_STATE_MAX = 256 * 1024
UI_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]{0,63}$")
SETTINGS_KEYS = {"unpaywall_email": str, "auto_open_access": bool, "save_mode": str}
# When saving from a publisher page in the extension: "link" = link + full text + metadata (default; no PDF
# file), "pdf" = the same and the PDF too.
SAVE_MODES = ("link", "pdf")

ctx = SimpleNamespace(store=None, vault=None, resolved=None, create_paper=None, with_extras=None,
                      match_url=None, same_title=None, guess_title=None, publish=None, hashes=None)


def install(app, *, store, resolved, create_paper, with_extras, match_url, same_title, guess_title,
            publish=None) -> None:
    ctx.store, ctx.resolved, ctx.vault = store, resolved, Path(resolved.vault)
    ctx.create_paper, ctx.with_extras, ctx.match_url = create_paper, with_extras, match_url
    ctx.same_title, ctx.guess_title, ctx.publish = same_title, guess_title, publish
    ctx.hashes = acquire.PdfHashes(store)
    app.include_router(router)


router = APIRouter()


# ---------------------------------------------------------------- app-wide settings (data dir)
def app_settings() -> dict:
    s = paths.load_settings()
    email = str(s.get("unpaywall_email") or os.environ.get("RB_UNPAYWALL_EMAIL", "")).strip()
    mode = s.get("save_mode") if s.get("save_mode") in SAVE_MODES else "link"
    return {"unpaywall_email": email, "auto_open_access": bool(s.get("auto_open_access", True)), "save_mode": mode}


class SettingsIn(BaseModel):
    unpaywall_email: str | None = None
    auto_open_access: bool | None = None
    save_mode: str | None = None


@router.get("/api/settings")
def get_settings():
    return app_settings()


@router.put("/api/settings")
def put_settings(body: SettingsIn):
    upd: dict = {}
    if body.unpaywall_email is not None:
        e = body.unpaywall_email.strip()
        if e and not acquire.EMAIL_RE.match(e):
            raise HTTPException(400, "That doesn't look like an email address")
        upd["unpaywall_email"] = e or None
    if body.auto_open_access is not None:
        upd["auto_open_access"] = body.auto_open_access
    if body.save_mode is not None:
        if body.save_mode not in SAVE_MODES:
            raise HTTPException(400, "save_mode must be \"link\" (link + text) or \"pdf\" (link + text + PDF)")
        upd["save_mode"] = body.save_mode
    if upd:
        paths.save_settings(upd)
    return app_settings()


# ---------------------------------------------------------------- UI state (vault/state/ui.json)
def _ui_path() -> Path:
    return ctx.vault / "state" / "ui.json"


def _ui_load() -> dict:
    try:
        d = json.loads(_ui_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


@router.get("/api/ui-state")
def get_ui_state():
    # restore: a fresh browser copies these into its localStorage (static/settings.js); RB_UI_RESTORE=0 turns
    # that off (the browser test servers do, so one test's settings never reload another test's page)
    return {"state": _ui_load(), "restore": os.environ.get("RB_UI_RESTORE", "1") != "0"}


@router.put("/api/ui-state")
async def put_ui_state(request: Request):
    """Merge {"state": {key: value | null}} into state/ui.json (null removes a key)."""
    raw = await request.body()
    if len(raw) > UI_STATE_MAX:
        raise HTTPException(413, "UI state is too large")
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(400, "Expected JSON")
    upd = body.get("state") if isinstance(body, dict) else None
    if not isinstance(upd, dict):
        raise HTTPException(400, "Expected {\"state\": {...}}")
    with ctx.store.lock:
        cur = _ui_load()
        for k, v in upd.items():
            if not UI_KEY_RE.match(str(k)):
                raise HTTPException(400, f"Invalid key {k!r}")
            if v is None:
                cur.pop(k, None)
            else:
                cur[k] = v
        if len(json.dumps(cur)) > UI_STATE_MAX:
            raise HTTPException(413, "UI state is too large")
        paths.write_json_atomic(_ui_path(), cur)
    return {"state": cur}


# ---------------------------------------------------------------- library info, profiles, restart
def supervised() -> bool:
    return bool(os.environ.get("RB_RESTART_FILE"))


def _restart(request: dict) -> bool:
    """Ask `research-brain serve` to restart this server (writes RB_RESTART_FILE, then SIGTERM to self)."""
    f = os.environ.get("RB_RESTART_FILE")
    if not f:
        return False
    try:
        Path(f).write_text(json.dumps(request), encoding="utf-8")
    except OSError:
        return False
    threading.Timer(0.6, _terminate).start()
    return True


def _terminate() -> None:  # replaced in tests
    os.kill(os.getpid(), signal.SIGTERM)


@router.get("/api/library")
def get_library():
    r = ctx.resolved
    meta = None
    try:
        meta = migrate.read_meta(ctx.vault)
    except migrate.SchemaError:
        pass
    return {"version": __version__, "schema": (meta or {}).get("schema"), "app_schema": migrate.SCHEMA_VERSION,
            "vault": str(ctx.vault), "profile": r.profile, "pinned": r.pinned, "legacy": r.legacy,
            "source": r.source, "notices": list(r.notices), "data_dir": str(paths.user_data_dir()),
            "supervised": supervised(), "move_legacy_pending": bool(paths.load_settings().get("move_legacy_on_start"))}


class ProfileIn(BaseModel):
    name: str


@router.get("/api/profiles")
def get_profiles():
    out = profiles.list_profiles(ctx.resolved)
    out["supervised"] = supervised()
    return out


@router.post("/api/profiles", status_code=201)
def post_profile(body: ProfileIn):
    try:
        return profiles.create(body.name)
    except profiles.ProfileError as e:
        raise HTTPException(e.status, str(e))


@router.post("/api/profiles/switch")
def switch_profile(body: ProfileIn):
    if ctx.resolved.pinned:
        raise HTTPException(409, "This server was started with a fixed library (--vault or RB_VAULT), so "
                                 "profiles can't be switched here. Restart it without that setting.")
    try:
        p = profiles.select(body.name.strip().lower())
    except profiles.ProfileError as e:
        raise HTTPException(e.status, str(e))
    if p["name"] == ctx.resolved.profile:
        return {"name": p["name"], "restarting": False, "restart_required": False}
    restarting = _restart({"profile": p["name"]})
    return {"name": p["name"], "path": p["path"], "restarting": restarting, "restart_required": not restarting}


@router.post("/api/library/move-legacy")
def move_legacy():
    if not ctx.resolved.legacy:
        raise HTTPException(409, "This library isn't in the old app-folder location; nothing to move.")
    paths.save_settings({"move_legacy_on_start": True})
    paths.log(f"move of legacy vault {ctx.vault} requested from the app")
    restarting = _restart({"profile": paths.DEFAULT_PROFILE})
    return {"restarting": restarting, "restart_required": not restarting,
            "command": "research-brain move-legacy-vault"}


# ---------------------------------------------------------------- open-access PDFs
def on_paper_added(nid: str, meta: dict | None = None) -> bool:
    """Called after a paper is added by DOI / arXiv id / title (never raises, never blocks)."""
    try:
        if not acquire.enabled():
            return False
        s = app_settings()
        if not s["auto_open_access"]:
            return False
        n = ctx.store.get(nid)
        if n.get("type") != "paper" or n.get("has_pdf") or not (n.get("doi") or n.get("arxiv") or (meta or {}).get("oa_pdf")):
            return False
        return acquire.schedule(ctx.store, nid, meta, email=s["unpaywall_email"], hashes=ctx.hashes, on_done=_acquired)
    except Exception as e:
        print(f"[research_brain] open-access lookup not started for {nid}: {e!r}")
        return False


def _acquired(nid: str, rec: dict) -> None:
    if ctx.publish and rec.get("state") == "found":
        ctx.publish({"type": "notes-changed", "ids": [nid]})


def _note_or_404(nid: str) -> dict:
    try:
        return ctx.store.get(nid)
    except KeyError:
        raise HTTPException(404, f"No note with id '{nid}'")


@router.get("/api/notes/{nid}/acquire")
def acquire_status(nid: str):
    n = _note_or_404(nid)
    out = acquire.status(ctx.store, nid)
    out["has_pdf"] = n["has_pdf"]
    return out


@router.post("/api/notes/{nid}/acquire", status_code=202)
def acquire_now(nid: str):
    n = _note_or_404(nid)
    if n["type"] != "paper":
        raise HTTPException(400, "Only papers have PDFs")
    if n["has_pdf"]:
        return {"state": "has-pdf", "started": False}
    if not acquire.enabled():
        raise HTTPException(503, "Open-access PDF lookup is turned off (RB_ACQUIRE=0)")
    started = acquire.schedule(ctx.store, nid, {"arxiv": n["arxiv"], "doi": n["doi"]},
                               email=app_settings()["unpaywall_email"], hashes=ctx.hashes, on_done=_acquired)
    return {"state": "running", "started": started}


# ---------------------------------------------------------------- PDF bytes from the browser extension
async def _stream_to_temp(request: Request, max_bytes: int, suffix: str, magic: bytes | None) -> Path:
    tmpdir = Path(tempfile.mkdtemp(prefix="rb_upload_"))
    dest = tmpdir / f"upload{suffix}"
    size, head = 0, b""
    try:
        clen = request.headers.get("content-length", "")
        if clen.isdigit() and int(clen) > max_bytes:
            raise HTTPException(413, f"The file is larger than {max_bytes >> 20} MB")
        with dest.open("wb") as f:
            async for chunk in request.stream():
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(413, f"The file is larger than {max_bytes >> 20} MB")
                if len(head) < 1024:
                    head += chunk[:1024 - len(head)]
                f.write(chunk)
        if size == 0:
            raise HTTPException(400, "The upload was empty")
        if magic is not None and magic not in head:
            raise HTTPException(415, "That file isn't a PDF")
        return dest
    except BaseException:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise


def _clean_source_url(u: str) -> str:
    u = (u or "").strip()[:2000]
    if not u:
        return ""
    try:
        s = urlsplit(u)
    except ValueError:
        return ""
    return u if s.scheme in ("http", "https") and s.hostname and not s.username and not s.password else ""


def _viewer(nid: str, **flags) -> dict:
    out = ctx.with_extras(ctx.store.get(nid))
    if out.get("has_pdf"):
        out["viewer"] = f"/viewer/{nid}"
    out["open"] = f"/#note={nid}"
    out.update({k: v for k, v in flags.items() if v})
    return out


def _attach(nid: str, tmp: Path, text: str) -> None:
    store = ctx.store
    with store.lock:
        shutil.move(str(tmp), store.pdf_path(nid))
        if text.strip():
            store.text_path(nid).write_text(text, encoding="utf-8")
        store.save(store.get(nid))


def _link_text(nid: str, text: str, url: str = "") -> bool:
    """A linked paper: keep the PDF's text (with its [[page N]] markers), not the file. Never touches a paper
    that has a PDF (except recording the url). True if text was written."""
    store = ctx.store
    wrote = False
    with store.lock:
        n = store.get(nid)
        if not n["has_pdf"]:
            if text.strip():
                store.text_path(nid).write_text(text, encoding="utf-8")
                wrote = True
            n["source"] = "linked"
            if n["type"] == "note":
                n["type"] = "paper"
        if url and not n["url"]:
            n["url"] = url
        store.save(n)
    return wrote


def _ingest(tmp: Path, source_url: str, doi: str, title: str, keep_pdf: bool = True, note_id: str = "") -> dict:
    from .pdftext import extract
    store = ctx.store
    target = None
    if note_id:  # "Get the PDF" for a paper already in the library (a linked paper's upgrade)
        target = _note_or_404(note_id)
        if target["type"] != "paper":
            raise HTTPException(400, "Only papers have PDFs")
        if target["has_pdf"]:
            return _viewer(target["id"], duplicate=True, existing=True)
    sha = acquire.sha256_file(tmp)
    owner = ctx.hashes.owner(sha)
    if owner:
        return _viewer(owner, duplicate=True, existing=True)
    info: dict = {}
    try:
        text, meta_title = extract(tmp, info=info)
    except Exception as e:
        raise HTTPException(400, f"Couldn't read that PDF: {e}")
    declared = metadata.ids_from_pdf_info(info)  # XMP / document info: the PDF's own DOI beats its text's

    def pdf_ids() -> dict:
        ids = scholar.find_ids_in_text(text)
        ids.update({k: v for k, v in declared.items() if v and k in ids})
        return ids
    if target is not None:
        _attach(target["id"], tmp, text)  # the PDF's page text replaces a linked article's section text
        if not target["doi"] and not target["arxiv"]:
            ids = pdf_ids()
            if ids["doi"] or ids["arxiv"]:
                store.set_fields(target["id"], doi=ids["doi"], arxiv=ids["arxiv"])
                _resolve(target["id"], ids)
        if source_url and not target["url"]:
            store.set_fields(target["id"], url=source_url)
        return _viewer(target["id"], attached=True)

    def keep(nid: str) -> None:
        if keep_pdf:
            _attach(nid, tmp, text)
        else:
            _link_text(nid, text, source_url)
    ids = pdf_ids()
    if doi:
        k, v = scholar.parse_identifier(doi)
        if k == "doi":
            ids["doi"] = v
    if source_url:
        k, v = scholar.parse_identifier(unquote(source_url))
        if k == "arxiv" and "arxiv.org" in source_url:
            ids["arxiv"] = v
        elif k == "doi" and not ids["doi"]:
            ids["doi"] = v
    have = ctx.match_url(source_url) if source_url else None
    if not have and (ids["doi"] or ids["arxiv"]):
        have = store.find_duplicate(ids["doi"], ids["arxiv"], "")
    if have:
        if have["has_pdf"]:
            return _viewer(have["id"], duplicate=True, existing=True)
        keep(have["id"])
        if not have["doi"] and not have["arxiv"] and (ids["doi"] or ids["arxiv"]):
            store.set_fields(have["id"], doi=ids["doi"], arxiv=ids["arxiv"])
        _resolve(have["id"], ids)
        return _viewer(have["id"], duplicate=True, attached=keep_pdf, linked=not keep_pdf)
    stem = Path(unquote(urlsplit(source_url).path)).stem.replace("_", " ") if source_url else ""
    guess = (title or "").strip()[:300] or meta_title or ctx.guess_title(text) or stem or "Untitled paper"
    meta = None
    try:
        if ids["arxiv"]:
            meta = scholar.lookup(f"arXiv:{ids['arxiv']}")
        if not meta and ids["doi"]:
            meta = scholar.lookup(ids["doi"])
        if not meta and guess:
            cand = scholar.lookup(guess)
            if cand and ctx.same_title(cand["title"], guess):
                meta = cand
    except scholar.ScholarError:
        meta = None
    meta = dict(meta or {})
    meta.setdefault("arxiv", ids["arxiv"])
    meta.setdefault("doi", ids["doi"])
    if not meta.get("url") and source_url:
        meta["url"] = source_url
    if not meta.get("title"):
        meta["title"] = guess
    dup = store.find_duplicate(meta.get("doi", ""), meta.get("arxiv", ""), meta.get("s2_id", ""))
    if dup:
        if dup["has_pdf"]:
            return _viewer(dup["id"], duplicate=True, existing=True)
        keep(dup["id"])
        _resolve(dup["id"], meta)
        return _viewer(dup["id"], duplicate=True, attached=keep_pdf, linked=not keep_pdf)
    note = ctx.create_paper(meta, meta_hint={"url": source_url}, wait=metadata.WAIT_ON_IMPORT)
    keep(note["id"])
    if not keep_pdf:
        on_linked_added(note["id"], meta)
    return _viewer(note["id"], linked=not keep_pdf)


@router.post("/api/import/pdf-bytes", status_code=201)
async def import_pdf_bytes(request: Request, source_url: str = "", doi: str = "", title: str = "", note: str = "",
                           keep_pdf: str = "1"):
    """The extension's "Save PDF to research_brain": the browser fetched the PDF with the user's own access
    and posts the bytes here. Body: the raw PDF, Content-Type application/pdf (a type no web page can send
    without a CORS preflight, which the origin guard refuses for other sites).
    ?note=<id>: attach to that paper (upgrades a linked paper; its text is re-extracted from the PDF, page
    markers replace section markers). ?keep_pdf=0: a linked paper from a PDF tab: the text (with [[page N]]
    markers) is kept, the file is not."""
    ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype != "application/pdf":
        raise HTTPException(415, "Send the PDF bytes with Content-Type: application/pdf")
    keep = keep_pdf.strip().lower() not in ("0", "false", "no")
    tmp = await _stream_to_temp(request, UPLOAD_MAX_MB << 20, ".pdf", b"%PDF")
    try:
        return await run_in_threadpool(_ingest, tmp, _clean_source_url(source_url), doi[:300], title, keep,
                                       note.strip()[:200])
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)


# ---------------------------------------------------------------- linked papers (link + text, no PDF file)
# The extension's default "Save to research_brain (link + text)" on a publisher page: it reads the article
# the user is looking at, in their own browser session, and posts its URL, citation metadata and full text
# here. Nothing is downloaded by the server and no credentials ever reach it. The text keeps
# [[section <heading>]] markers (HTML articles) or [[page N]] markers (a PDF read in the browser), so passages
# and citations point to a section ("§3.2") or a page.
LINKED_MAX_CHARS = int(os.environ.get("RB_LINKED_MAX_CHARS", str(5_000_000)))
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f​-‏  ⁠﻿]")
_PAGE_MARK_RE = re.compile(r"\[\[page \d+\]\]")
_SECTION_MARK_RE = re.compile(r"\[\[section [^\[\]\n]*\]\]")
_SECTION_FIX_RE = re.compile(r"\[\[section ([^\[\]\n]*)\]\]")


class LinkedIn(BaseModel):
    url: str
    title: str = ""
    authors: list[str] = []
    doi: str = ""
    arxiv: str = ""
    year: int | str | None = None
    venue: str = ""
    abstract: str = ""
    text: str = ""
    kind: str = "html"           # html: [[section …]] markers; pdf: [[page N]] markers
    references: list[str] = []   # the article's reference list (used to link `cites` to library papers)
    pdf_url: str = ""            # the page's citation_pdf_url, kept for "Get the PDF"
    # more of the page's citation metadata (all optional; extension/extract.js): fed to the metadata resolver
    # (app/metadata.py) as identifiers and as the "page" record that fills what the authoritative record lacks
    pmid: str = ""
    isbn: str = ""
    osti: str = ""
    ntrs: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""
    issn: list[str] = []
    publisher: str = ""
    journal_abbrev: str = ""
    conference: str = ""
    genre: str = ""              # a CSL type the page implies: article-journal, paper-conference, report, thesis...
    date: str = ""               # the full publication date the page gives (YYYY-MM-DD or YYYY/MM/DD)


def _line(v, n: int) -> str:
    """One line: control and invisible characters removed, whitespace (newlines too) collapsed."""
    return " ".join(_CTRL_RE.sub("", str(v or "")).split())[:n]


def _clean_text(t: str, kind: str) -> str:
    t = _CTRL_RE.sub("", (t or "").replace("\r\n", "\n").replace("\r", "\n"))
    if kind == "html":
        t = _PAGE_MARK_RE.sub("", t)  # an HTML article has no pages
        t = _SECTION_FIX_RE.sub(lambda m: f"[[section {_line(m.group(1), 200)}]]" if _line(m.group(1), 200) else "", t)
    else:
        t = _SECTION_MARK_RE.sub("", t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _year(v) -> int | None:
    m = re.search(r"\b(1[5-9]\d\d|2[01]\d\d)\b", str(v if v is not None else ""))
    return int(m.group(1)) if m else None


def _ident(raw: str, kind: str) -> str:
    raw = (raw or "").strip()[:300]
    if not raw:
        return ""
    if kind == "arxiv" and not re.match(r"(?i)^arxiv:", raw) and "arxiv.org" not in raw.lower():
        raw = f"arXiv:{raw}"
    k, v = scholar.parse_identifier(unquote(raw))
    return v if k == kind else ""


def _cites_from_references(nid: str, refs: list[str]) -> int:
    """Library papers named (by DOI or arXiv id) in the article's reference list become `cites`."""
    if not refs:
        return 0
    from . import citations
    store = ctx.store
    lib = citations.LibraryIndex(store.all())
    found = []
    for r in refs:
        m = scholar.DOI_RE.search(r)
        a = scholar.ARXIV_URL_RE.search(r) or re.search(r"(?i)arxiv[:\s]+(\d{4}\.\d{4,5})", r)
        ref = {"doi": scholar.clean_doi(m.group(1)) if m else "", "arxiv": re.sub(r"v\d+$", "", a.group(1)) if a else ""}
        if not (ref["doi"] or ref["arxiv"]):
            continue
        t = lib.match(ref)
        if t and t != nid and t not in found:
            found.append(t)
    if not found:
        return 0
    cur = store.get(nid)["cites"]
    merged = cur + [t for t in found if t not in cur]
    citations._set_cites(store, nid, merged)
    return len(merged) - len(cur)


def on_linked_added(nid: str, meta: dict | None = None) -> bool:
    """After a linked paper is added: no PDF is fetched, unless Library settings say "Link + text + PDF" AND
    open-access downloads are on (then only a free legal copy, exactly as for papers added by DOI)."""
    try:
        if app_settings()["save_mode"] != "pdf":
            return False
        return on_paper_added(nid, meta)
    except Exception as e:
        print(f"[research_brain] open-access lookup for linked paper {nid} skipped: {e!r}")
        return False


@router.get("/api/import/linked/prefs")
def linked_prefs():
    """What the extension's primary button does: {"save_mode": "link" | "pdf"} (Library settings)."""
    return {"save_mode": app_settings()["save_mode"]}


def _import_linked(body: LinkedIn) -> dict:
    store = ctx.store
    url = _clean_source_url(body.url)
    if not url:
        raise HTTPException(400, "Send the article's http(s) address (url)")
    kind = (body.kind or "html").strip().lower()
    if kind not in ("html", "pdf"):
        raise HTTPException(400, "kind must be html or pdf")
    if len(body.text or "") > LINKED_MAX_CHARS:
        raise HTTPException(413, f"The article text is longer than {LINKED_MAX_CHARS // 1_000_000} million characters")
    text = _clean_text(body.text, kind)
    title = _line(body.title, 500)
    authors = [a for a in (_line(x, 200) for x in (body.authors or [])[:300]) if a]
    doi = _ident(body.doi, "doi")
    arxiv = _ident(body.arxiv, "arxiv")
    low = unquote(url).lower()
    if not arxiv and "arxiv.org/" in low:
        arxiv = _ident(url, "arxiv")
    if not doi and ("doi" in low or scholar.DOI_RE.search(unquote(url))):
        doi = _ident(url, "doi")
    page = {"title": title, "authors": authors, "year": _year(body.year), "venue": _line(body.venue, 300),
            "doi": doi, "arxiv": arxiv, "url": url,
            "abstract": _CTRL_RE.sub("", (body.abstract or "").replace("\r", "")).strip()[:20000]}
    pdf_url = _clean_source_url(body.pdf_url)
    refs = [r for r in (_line(x, 1000) for x in (body.references or [])[:3000]) if r]
    ids = {"doi": doi, "arxiv": arxiv, "pmid": _line(body.pmid, 20), "isbn": _line(body.isbn, 30),
           "osti": _line(body.osti, 20), "ntrs": _line(body.ntrs, 20)}
    page_rec = dict(page, volume=_line(body.volume, 40), issue=_line(body.issue, 40), pages=_line(body.pages, 60),
                    issn=[_line(x, 20) for x in (body.issn or [])[:4]], publisher=_line(body.publisher, 300),
                    journal_abbrev=_line(body.journal_abbrev, 200), conference=_line(body.conference, 300),
                    genre=_line(body.genre, 40), date=_line(body.date, 40), pmid=ids["pmid"], isbn=ids["isbn"],
                    osti=ids["osti"], ntrs=ids["ntrs"])
    if not (text or page["abstract"] or title or doi or arxiv):
        raise HTTPException(400, "Nothing to save: no text, title or identifier came with the page")

    have = (store.find_duplicate(doi, arxiv, "") if (doi or arxiv) else None) or ctx.match_url(url)
    if have is None and pdf_url:
        have = ctx.match_url(pdf_url)
    if have is not None:
        nid = have["id"]
        wrote = False
        with store.lock:
            n = store.get(nid)
            if n["type"] == "note":
                n["type"] = "paper"  # a source note made by clipping this page becomes the paper
            for k, v in page.items():  # fill what the library entry lacks; never overwrite the user's edits
                if v not in (None, "", []) and not n.get(k):
                    n[k] = v
            if not n["has_pdf"]:
                old = store.text_path(nid)
                old_len = old.stat().st_size if old.exists() else 0
                if text and (not old_len or (n.get("source") == "linked" and len(text.encode()) > old_len)):
                    old.write_text(text, encoding="utf-8")
                    wrote = True
                n["source"] = "linked"
            store.save(n)
        added = _cites_from_references(nid, refs)
        _resolve(nid, ids, page=page_rec, url=url)
        if ctx.publish:
            ctx.publish({"type": "notes-changed", "ids": [nid]})
        return _viewer(nid, duplicate=True, existing=bool(have["has_pdf"]), text_attached=wrote, cites_added=added,
                       save_mode=app_settings()["save_mode"], pdf_url=pdf_url)

    meta = dict(page)
    if (doi or arxiv) and (not title or not authors):  # thin page metadata: fill it in from Semantic Scholar
        try:
            s2 = scholar.lookup(f"arXiv:{arxiv}" if arxiv else doi)
        except scholar.ScholarError:
            s2 = None
        for k, v in (s2 or {}).items():
            if k in ("title", "authors", "year", "venue", "doi", "arxiv", "s2_id", "abstract") and v and not meta.get(k):
                meta[k] = v
    if not meta.get("title"):
        first = next((ln for ln in text.splitlines() if ln.strip() and not ln.startswith("[[")), "")
        meta["title"] = _line(first, 200) or urlsplit(url).hostname or "Untitled article"
    note = ctx.create_paper(meta, meta_hint={"page": page_rec, "ids": ids, "url": url}, wait=metadata.WAIT_ON_IMPORT)
    nid = note["id"]
    with store.lock:
        n = store.get(nid)
        if text:
            store.text_path(nid).write_text(text, encoding="utf-8")
        n["source"] = "linked"
        store.save(n)
    added = _cites_from_references(nid, refs)
    on_linked_added(nid, {**meta, "oa_pdf": ""})
    if ctx.publish:
        ctx.publish({"type": "notes-changed", "ids": [nid]})
    return _viewer(nid, text_attached=bool(text), cites_added=added, save_mode=app_settings()["save_mode"],
                   pdf_url=pdf_url)


def _resolve(nid: str, ids: dict | None = None, page: dict | None = None, url: str = "") -> None:
    """A paper already in the library got new identifiers or page metadata: resolve it in the background
    (fills what it lacks; a paper resolved recently is left alone)."""
    ids = {k: v for k, v in (ids or {}).items() if k in ("doi", "arxiv", "pmid", "isbn", "osti", "ntrs") and v}
    metadata.schedule(ctx.store, nid, publish=ctx.publish, ids=ids, page=page, url=url)


@router.post("/api/import/linked", status_code=201)
def import_linked(body: LinkedIn):
    """Save a paper as link + full text + metadata, without its PDF file (see the section comment)."""
    return _import_linked(body)


# ---------------------------------------------------------------- whole-vault zip
@router.get("/api/vault/export")
async def vault_export(pdfs: bool = True):
    tmpdir = Path(tempfile.mkdtemp(prefix="rb_export_"))
    name = f"research_brain-{ctx.resolved.profile or 'library'}-{dt.date.today().isoformat()}.zip"
    dest = tmpdir / name
    try:
        await run_in_threadpool(vaultzip.export_zip, ctx.vault, dest, pdfs)
    except Exception:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise
    paths.log(f"exported {ctx.vault} to a zip download (pdfs={pdfs})")
    return FileResponse(dest, media_type="application/zip", filename=name,
                        background=BackgroundTask(shutil.rmtree, tmpdir, True))


@router.post("/api/vault/import", status_code=201)
async def vault_import(request: Request, profile: str):
    name = (profile or "").strip().lower()
    if not paths.valid_profile(name):
        raise HTTPException(400, "Profile names use lowercase letters, digits, - and _ (at most 40 characters)")
    if name in profiles.names() and paths.profile_path(name).exists():
        raise HTTPException(409, f"A profile named {name!r} already exists; pick a new name")
    ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype not in ("application/zip", "application/x-zip-compressed"):
        raise HTTPException(415, "Send the zip with Content-Type: application/zip")
    tmp = await _stream_to_temp(request, vaultzip.MAX_TOTAL, ".zip", b"PK")
    target = paths.user_data_dir() / "profiles" / name

    def work():
        res = vaultzip.import_zip(tmp, target)
        try:
            migrate.run(target, log=lambda m: None)
        except migrate.SchemaError:
            shutil.rmtree(target, ignore_errors=True)  # a library from a newer app: don't leave a broken profile
            raise
        paths.log(f"imported a library zip into profile {name} ({res['notes']} notes)")
        return res
    try:
        res = await run_in_threadpool(work)
    except vaultzip.ZipImportError as e:
        raise HTTPException(e.status, str(e))
    except migrate.SchemaError as e:
        raise HTTPException(409, str(e))
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)
    res["profile"] = name
    return res
