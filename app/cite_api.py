"""HTTP endpoints for citing: styles, formatted citations, citation keys, .bib/RIS/CSL-JSON exports and
auto-updating .bib files. server.py calls install(app, ...) once. See API.md "Citing".

    GET    /api/cite/styles                     built-in + your styles, the default style and copy format
    POST   /api/cite/styles {filename, content} add a .csl file to <vault>/styles/ (validated, see SECURITY.md)
    DELETE /api/cite/styles/{id}                remove one of your styles
    POST   /api/cite/format {ids|topic|note, style?, mode, format}   citation or bibliography (html/text)
    POST   /api/cite/keys {ids|topic|note}      citation keys (saved to the notes) and "\\cite{...}"
    PUT    /api/cite/keys/{id} {citekey}        set a note's key by hand
    GET    /api/cite/search?q=                  papers to cite (title, authors, year, key)
    GET/PUT /api/cite/settings                  {style, copy}
    GET    /api/cite/auto-exports               auto-updating .bib files (+ status)
    POST   /api/cite/auto-exports {scope, flavor, path}     DELETE /api/cite/auto-exports/{id}
    GET    /api/export/bibtex?ids=|topic=|note=|all=1&flavor=bibtex|biblatex
    GET    /api/export/ris?...    GET /api/export/csl-json?...
"""
from __future__ import annotations

import datetime as dt
import os
import re
import secrets
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from . import bibexport, cite, paths

router = APIRouter()
ctx = SimpleNamespace(store=None, vault=None, note_refs=None, exporter=None)
COPY_FORMATS = ("citekey", "formatted")
FLAVORS = ("bibtex", "biblatex")
MAX_AUTO_EXPORTS = 20
MAX_IDS = 5000


def install(app, *, store, vault, note_refs=None, watcher=None) -> None:
    """note_refs(nid) -> ids the note links to with [[ ]]; watcher: live.VaultWatcher (outside edits)."""
    ctx.store, ctx.vault, ctx.note_refs = store, Path(vault), note_refs
    ctx.exporter = AutoExporter(store, Path(vault))
    if watcher is not None:
        prev = watcher.on_change

        def on_change(ids, _prev=prev):
            if _prev:
                _prev(ids)
            ctx.exporter.poke()
        watcher.on_change = on_change
    app.include_router(router)

    @app.on_event("startup")
    def _start_auto_export():
        if os.environ.get("RB_AUTO_EXPORT", "1") != "0":
            ctx.exporter.start()

    @app.on_event("shutdown")
    def _stop_auto_export():
        ctx.exporter.stop()


# ---------------------------------------------------------------------------------------------- settings
def _settings() -> dict:
    s = paths.load_settings()
    style = s.get("cite_style") if isinstance(s.get("cite_style"), str) else cite.DEFAULT_STYLE
    copy = s.get("cite_copy") if s.get("cite_copy") in COPY_FORMATS else "formatted"
    return {"style": style, "copy": copy}


def _vault_key(vault: Path) -> str:
    try:
        return str(Path(vault).resolve())
    except OSError:
        return str(vault)


def _load_exports(vault: Path) -> list[dict]:
    allv = paths.load_settings().get("cite_auto_exports")
    lst = (allv or {}).get(_vault_key(vault)) if isinstance(allv, dict) else None
    out = []
    for e in lst or []:
        if isinstance(e, dict) and e.get("id") and e.get("path"):
            out.append({"id": str(e["id"]), "scope": str(e.get("scope") or "all"),
                        "flavor": e.get("flavor") if e.get("flavor") in FLAVORS else "biblatex", "path": str(e["path"])})
    return out


def _save_exports(vault: Path, lst: list[dict]) -> None:
    allv = paths.load_settings().get("cite_auto_exports")
    allv = dict(allv) if isinstance(allv, dict) else {}
    if lst:
        allv[_vault_key(vault)] = lst
    else:
        allv.pop(_vault_key(vault), None)
    paths.save_settings({"cite_auto_exports": allv or None})


# ---------------------------------------------------------------------------------------------- scopes
def _notes_by_id() -> dict[str, dict]:
    return {n["id"]: n for n in ctx.store.all()}


def resolve_scope(by_id: dict, ids=None, topic: str = "", note: str = "", all_: bool = False) -> list[str]:
    """Note ids to cite. Fragments stand for their paper. topic / note / all take papers only."""
    out: list[str] = []

    def add(nid, papers_only):
        n = by_id.get(nid)
        if n is None:
            return
        if n["type"] == "fragment":
            n = by_id.get(n.get("parent") or "")
            if n is None:
                return
        if papers_only and n["type"] != "paper":
            return
        if n["id"] not in out:
            out.append(n["id"])

    for nid in ids or []:
        add(str(nid), False)
    if topic:
        from . import topics as topics_mod
        try:
            t = topics_mod.get_topic(ctx.store, topic)
        except KeyError:
            raise HTTPException(404, f"No topic '{topic}'")
        for nid in t["members"]:
            add(nid, True)
    if note:
        if note not in by_id:
            raise HTTPException(404, f"No note '{note}'")
        refs = list(ctx.note_refs(note)) if ctx.note_refs else []
        refs += [x for x in by_id[note].get("links") or [] if x not in refs]
        if by_id[note]["type"] == "paper":
            refs.insert(0, note)
        for nid in refs:
            add(nid, True)
    if all_:
        for n in sorted(by_id.values(), key=lambda n: n["id"]):
            if n["type"] == "paper":
                add(n["id"], True)
    if len(out) > MAX_IDS:
        raise HTTPException(400, f"At most {MAX_IDS} items at once")
    return out


def _entries(ids: list[str], by_id: dict, persist: bool = True) -> list[tuple[str, dict]]:
    """[(citekey, csl item)] sorted by key. persist=True saves keys handed out for the first time."""
    if persist:
        keys = cite.ensure_keys(ctx.store, ids, list(by_id.values()))
    else:
        keys = {k: v[0] for k, v in cite.assign_keys(list(by_id.values())).items()}
    ents = [(keys[i], cite.csl_for(by_id[i])) for i in ids if i in keys]
    ents.sort(key=lambda e: e[0].lower())
    return ents


# ---------------------------------------------------------------------------------------------- models
class ScopeIn(BaseModel):
    ids: list[str] = []
    topic: str = ""
    note: str = ""


class FormatIn(ScopeIn):
    style: str = ""
    mode: str = "bibliography"
    format: str = "html"


class KeyIn(BaseModel):
    citekey: str


class StyleIn(BaseModel):
    filename: str
    content: str


class AutoExportIn(BaseModel):
    scope: str = "all"
    flavor: str = "biblatex"
    path: str


# ---------------------------------------------------------------------------------------------- styles
@router.get("/api/cite/styles")
def api_styles():
    s = _settings()
    styles = cite.list_styles(ctx.vault)
    if not any(x["id"] == s["style"] and not x["error"] for x in styles):
        s["style"] = cite.DEFAULT_STYLE
    return {"styles": styles, "default": s["style"], "copy": s["copy"],
            "user_dir": str(cite.user_styles_dir(ctx.vault))}


@router.post("/api/cite/styles", status_code=201)
def api_style_add(body: StyleIn):
    raw = body.content.encode("utf-8")
    try:
        return cite.save_user_style(ctx.vault, body.filename, raw)
    except cite.StyleError as e:
        raise HTTPException(400, str(e))


@router.delete("/api/cite/styles/{sid}")
def api_style_delete(sid: str):
    try:
        cite.delete_user_style(ctx.vault, sid)
    except KeyError:
        raise HTTPException(404, "Only styles you added can be removed")
    return {"ok": True}


# ---------------------------------------------------------------------------------------------- format / keys
@router.post("/api/cite/format")
def api_format(body: FormatIn):
    if body.mode not in ("citation", "bibliography"):
        raise HTTPException(400, 'mode must be "citation" or "bibliography"')
    if body.format not in ("html", "text"):
        raise HTTPException(400, 'format must be "html" or "text"')
    by_id = _notes_by_id()
    ids = resolve_scope(by_id, body.ids, body.topic, body.note)
    style = body.style or _settings()["style"]
    items = [cite.csl_for(by_id[i]) for i in ids]
    if body.topic or body.note:  # a list without an order of its own: alphabetical by key, as the .bib
        keys = {k: v[0] for k, v in cite.assign_keys(list(by_id.values())).items()}
        items.sort(key=lambda it: keys.get(it["id"], it["id"]).lower())
    try:
        out = cite.format_items(items, style, body.mode, ctx.vault)
    except KeyError:
        raise HTTPException(404, f"No style '{style}'")
    except cite.StyleError as e:
        raise HTTPException(422, str(e))
    out["ids"] = ids
    if body.format == "text":
        out.pop("html", None)
        for e in out["entries"]:
            e.pop("html", None)
    return out


@router.post("/api/cite/keys")
def api_keys(body: ScopeIn):
    by_id = _notes_by_id()
    ids = resolve_scope(by_id, body.ids, body.topic, body.note)
    keys = cite.ensure_keys(ctx.store, ids, list(by_id.values()))
    ordered = [keys[i] for i in ids if i in keys]
    return {"keys": keys, "ids": ids, "latex": "\\cite{" + ",".join(ordered) + "}" if ordered else "",
            "pandoc": "[" + "; ".join("@" + k for k in ordered) + "]" if ordered else ""}


@router.put("/api/cite/keys/{nid}")
def api_set_key(nid: str, body: KeyIn):
    key = body.citekey.strip()
    if not cite.KEY_RE.match(key):
        raise HTTPException(400, "A citation key may use letters, digits and _ : . + / - (no spaces), up to 100 characters")
    by_id = _notes_by_id()
    if nid not in by_id:
        raise HTTPException(404, f"No note '{nid}'")
    for other, (k, _p) in cite.assign_keys(list(by_id.values())).items():
        if other != nid and k.lower() == key.lower() and cite.stored_key(by_id[other]):
            raise HTTPException(409, f"“{by_id[other]['title']}” already has the key {k}")
    n = cite.set_citekey(ctx.store, nid, key)
    if ctx.exporter:
        ctx.exporter.poke()
    return {"id": nid, "citekey": cite.stored_key(n)}


@router.get("/api/cite/search")
def api_cite_search(q: str = "", limit: int = 20):
    limit = max(1, min(limit, 50))
    store = ctx.store
    hits = None
    if store._meta_ok():  # the library's full-text search (the same as /api/search)
        hits = store.meta.search(q, limit * 3, False) if q.strip() else store.meta.recent(limit * 3, False)
    by_id = _notes_by_id()
    if hits is None:
        ql = q.strip().lower()
        hits = [n for n in by_id.values() if n["type"] == "paper" and (not ql or ql in n["title"].lower()
                or any(ql in a.lower() for a in n["authors"]))]
        hits.sort(key=lambda n: n.get("updated") or "", reverse=True)
    keys = cite.assign_keys(list(by_id.values()))
    out = []
    for h in hits:
        n = by_id.get(h["id"])
        if n is None or n["type"] != "paper":
            continue
        item = cite.csl_for(n)
        out.append({"id": n["id"], "title": n["title"], "year": cite.year_of(item) or n.get("year"),
                    "authors": [cite.name_text(a) for a in (item.get("author") or [])[:3]],
                    "more_authors": max(0, len(item.get("author") or []) - 3),
                    "venue": item.get("container-title-short") or item.get("container-title") or "",
                    "citekey": keys.get(n["id"], ("", False))[0]})
        if len(out) >= limit:
            break
    return out


@router.get("/api/cite/settings")
def api_cite_settings():
    return _settings()


@router.put("/api/cite/settings")
def api_cite_settings_put(body: dict):
    """{style?, copy?}: the default style and what "Copy" puts on the clipboard."""
    upd = {}
    style, copy = body.get("style"), body.get("copy")
    if style is not None:
        if not isinstance(style, str) or style not in {s["id"] for s in cite.list_styles(ctx.vault)}:
            raise HTTPException(400, f"No style '{style}'")
        upd["cite_style"] = style
    if copy is not None:
        if copy not in COPY_FORMATS:
            raise HTTPException(400, 'copy must be "citekey" (\\cite{key}) or "formatted"')
        upd["cite_copy"] = copy
    if upd:
        paths.save_settings(upd)
    return _settings()


# ---------------------------------------------------------------------------------------------- exports
def _scope_from_query(request: Request) -> tuple[list[str], dict]:
    qp = request.query_params
    ids = [x for v in qp.getlist("ids") for x in v.split(",") if x.strip()]
    by_id = _notes_by_id()
    all_ = qp.get("all") in ("1", "true", "yes")
    if not (ids or qp.get("topic") or qp.get("note") or all_):
        raise HTTPException(400, "Say what to export: ids=a,b  or topic=<slug>  or note=<id>  or all=1")
    return resolve_scope(by_id, ids, qp.get("topic", ""), qp.get("note", ""), all_), by_id


def _filename(request: Request, ext: str) -> str:
    qp = request.query_params
    base = qp.get("topic") or qp.get("note") or ("library" if qp.get("all") else "references")
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-") or "references"
    return f"{base}.{ext}"


def _download(text: str, media: str, filename: str, request: Request) -> Response:
    disp = "inline" if request.query_params.get("inline") in ("1", "true") else "attachment"
    return Response(text, media_type=media, headers={"Content-Disposition": f'{disp}; filename="{filename}"'})


@router.get("/api/export/bibtex")
def api_export_bibtex(request: Request):
    flavor = request.query_params.get("flavor", "biblatex")
    if flavor not in FLAVORS:
        raise HTTPException(400, 'flavor must be "bibtex" or "biblatex"')
    ids, by_id = _scope_from_query(request)
    text = bibexport.to_bibtex(_entries(ids, by_id), flavor)
    return _download(text, "application/x-bibtex; charset=utf-8", _filename(request, "bib"), request)


@router.get("/api/export/ris")
def api_export_ris(request: Request):
    ids, by_id = _scope_from_query(request)
    return _download(bibexport.to_ris(_entries(ids, by_id)), "application/x-research-info-systems; charset=utf-8",
                     _filename(request, "ris"), request)


@router.get("/api/export/csl-json")
def api_export_csl_json(request: Request):
    ids, by_id = _scope_from_query(request)
    return _download(bibexport.to_csl_json(_entries(ids, by_id)), "application/vnd.citationstyles.csl+json; charset=utf-8",
                     _filename(request, "json"), request)


# ---------------------------------------------------------------------------------------------- auto-export
class PathError(ValueError):
    pass


def _app_dirs() -> list[Path]:
    out = [Path(__file__).resolve().parent.parent]
    try:
        out.append(Path(paths.static_dir()).resolve().parent)
    except Exception:
        pass
    return out


def _inside(p: Path, root: Path) -> bool:
    try:
        p.relative_to(root)
        return True
    except ValueError:
        return False


def validate_path(raw: str, vault: Path | None = None) -> Path:
    """The one file an auto-export may write: absolute, *.bib, its folder exists, not a symlink, not in the
    app's own folder or the library's internal folders, and either new/empty or written by research brain."""
    s = str(raw or "").strip()
    if not s or "\x00" in s:
        raise PathError("Give the full path of a .bib file, e.g. /Users/you/thesis/references.bib")
    s = os.path.expanduser(s)
    if not os.path.isabs(s):
        raise PathError("The path must be absolute (start with / or a drive letter), e.g. /Users/you/thesis/references.bib")
    if any(part == ".." for part in Path(s).parts):
        raise PathError("The path may not contain “..”")
    p = Path(os.path.normpath(s))
    if p.suffix.lower() != ".bib":
        raise PathError("The file name must end in .bib")
    if p.name.startswith("."):
        raise PathError("Use a visible file name (not starting with a dot)")
    parent = p.parent
    if not parent.is_dir():
        raise PathError(f"The folder {parent} doesn't exist. Create it first.")
    real_parent = parent.resolve()
    target = real_parent / p.name
    for root in _app_dirs():
        if _inside(target, root):
            raise PathError("Choose a folder outside research brain's own program folder.")
    if vault is not None:
        vr = Path(vault).resolve()
        for sub in ("notes", "pdfs", "text", ".index", ".trash", "state", "topics", "styles", "memory"):
            if _inside(target, vr / sub):
                raise PathError("Choose a folder outside the library's internal folders (the library folder itself is fine).")
    if os.path.islink(p) or os.path.islink(target):
        raise PathError("That path is a symbolic link. Point the export at the real file instead.")
    if target.exists():
        if not target.is_file():
            raise PathError("That path exists and isn't a regular file.")
        try:
            with open(target, "rb") as f:
                head = f.read(len(bibexport.HEADER.encode()) + 4)
        except OSError as e:
            raise PathError(f"Can't read the existing file: {e.strerror or e}")
        if head.strip() and not head.lstrip(b"\xef\xbb\xbf").startswith(bibexport.HEADER.encode()):
            raise PathError("That file already exists and wasn't written by research brain. Choose a new file name "
                            "(research brain won't overwrite a .bib you wrote yourself).")
    return target


def write_atomic(target: Path, text: str) -> None:
    """Replace `target` with `text` via a temp file in the same folder. Re-checks for a symlink first."""
    if os.path.islink(target):
        raise PathError("The export file has become a symbolic link; not writing.")
    mode = None
    try:
        mode = target.stat().st_mode & 0o777
    except FileNotFoundError:
        pass
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".rb-tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        if os.path.islink(target):
            raise PathError("The export file has become a symbolic link; not writing.")
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class AutoExporter:
    """Rewrites each configured .bib file when a note in its scope changes: a background thread checks a
    cheap signature (the store's write counter, topic files, outside edits reported by the file watcher)
    and re-renders; a file is written only when its text actually changed."""

    INTERVAL = 1.5

    def __init__(self, store, vault: Path):
        self.store, self.vault = store, Path(vault)
        self.status: dict[str, dict] = {}
        self._event = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pokes = 0
        self._last_sig = None
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="rb-auto-export", daemon=True)
        self._thread.start()
        self.poke()

    def stop(self) -> None:
        self._stop.set()
        self._event.set()
        if self._thread:
            self._thread.join(timeout=3)

    def poke(self) -> None:
        self._pokes += 1
        self._event.set()

    def _topics_sig(self) -> tuple:
        d = self.vault / "topics"
        try:
            return tuple(sorted((p.name, p.stat().st_mtime_ns) for p in d.glob("*.md")))
        except OSError:
            return ()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._event.wait(self.INTERVAL)
            self._event.clear()
            if self._stop.is_set():
                break
            try:
                sig = (self.store.rev, self._pokes, self._topics_sig())
                if sig != self._last_sig:
                    self._last_sig = sig
                    self.run_all()
            except Exception as e:  # never let the thread die
                print(f"[research_brain] auto-export failed: {e!r}")

    def run_all(self) -> list[dict]:
        exports = _load_exports(self.vault)
        if not exports:
            return []
        with self._lock:
            by_id = {n["id"]: n for n in self.store.all()}
            return [self.run_one(e, by_id) for e in exports]

    def run_one(self, e: dict, by_id: dict | None = None) -> dict:
        st = self.status.setdefault(e["id"], {})
        try:
            if by_id is None:
                by_id = {n["id"]: n for n in self.store.all()}
            target = validate_path(e["path"], self.vault)
            scope = e["scope"]
            if scope == "all":
                ids = resolve_scope(by_id, all_=True)
            elif scope.startswith("topic:"):
                ids = resolve_scope(by_id, topic=scope[6:])
            else:
                raise PathError(f"Unknown scope {scope}")
            ents = _entries(ids, by_id)
            header = (f"{bibexport.HEADER} (auto-export: {_scope_label(scope)}, {e['flavor']}). "
                      "This file is rewritten whenever the library changes; edit the papers in research brain instead.\n"
                      f"% {len(ents)} {'entry' if len(ents) == 1 else 'entries'}")
            text = bibexport.to_bibtex(ents, e["flavor"], header=header)
            try:
                old = target.read_text(encoding="utf-8")
            except (FileNotFoundError, UnicodeDecodeError):
                old = None
            if old != text:
                write_atomic(target, text)
                st["written"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
            st.update(error="", entries=len(ents), checked=time.time())
        except HTTPException as ex:
            st.update(error=str(ex.detail), checked=time.time())
        except (PathError, OSError) as ex:
            st.update(error=str(ex) if isinstance(ex, PathError) else f"Couldn't write the file: {ex.strerror or ex}",
                      checked=time.time())
        return {**e, **self.public_status(e)}

    def public_status(self, e: dict) -> dict:
        st = self.status.get(e["id"], {})
        written = st.get("written", "")
        if not written:
            try:
                p = Path(e["path"])
                if p.is_file():
                    with open(p, "rb") as f:
                        if f.read(len(bibexport.HEADER)).decode("utf-8", "ignore") == bibexport.HEADER:
                            written = dt.datetime.fromtimestamp(p.stat().st_mtime).astimezone().isoformat(timespec="seconds")
            except OSError:
                pass
        return {"written": written, "error": st.get("error", ""), "entries": st.get("entries")}


def _scope_label(scope: str) -> str:
    return "whole library" if scope == "all" else f"topic {scope[6:]}"


@router.get("/api/cite/auto-exports")
def api_auto_exports():
    lst = _load_exports(ctx.vault)
    return {"exports": [{**e, **ctx.exporter.public_status(e)} for e in lst]}


@router.post("/api/cite/auto-exports", status_code=201)
def api_auto_export_add(body: AutoExportIn):
    if body.flavor not in FLAVORS:
        raise HTTPException(400, 'flavor must be "bibtex" or "biblatex"')
    scope = body.scope.strip()
    if scope != "all":
        m = re.fullmatch(r"topic:([a-z0-9][a-z0-9-]*)", scope)
        if not m:
            raise HTTPException(400, 'scope must be "all" or "topic:<slug>"')
        from . import topics as topics_mod
        try:
            topics_mod.get_topic(ctx.store, m.group(1))
        except KeyError:
            raise HTTPException(404, f"No topic '{m.group(1)}'")
    try:
        target = validate_path(body.path, ctx.vault)
    except PathError as e:
        raise HTTPException(400, str(e))
    lst = _load_exports(ctx.vault)
    if any(Path(e["path"]) == target or Path(os.path.normpath(os.path.expanduser(e["path"]))) == target for e in lst):
        raise HTTPException(409, "That file is already an auto-export")
    if len(lst) >= MAX_AUTO_EXPORTS:
        raise HTTPException(400, f"At most {MAX_AUTO_EXPORTS} auto-exports")
    e = {"id": secrets.token_hex(4), "scope": scope, "flavor": body.flavor, "path": str(target)}
    lst.append(e)
    _save_exports(ctx.vault, lst)
    res = ctx.exporter.run_one(e)  # write it now, so errors show up right away
    return res


@router.delete("/api/cite/auto-exports/{eid}")
def api_auto_export_delete(eid: str):
    lst = _load_exports(ctx.vault)
    keep = [e for e in lst if e["id"] != eid]
    if len(keep) == len(lst):
        raise HTTPException(404, "No such auto-export")
    _save_exports(ctx.vault, keep)
    ctx.exporter.status.pop(eid, None)
    return {"ok": True, "note": "The .bib file was left where it is."}
