"""Whole-library zip export and import (moving to a new laptop, sharing a copy, a manual backup).

Export: every file that belongs to the library: the documents (Papers/, Notes/, Questions/, Highlights/,
Topics/, and PDFs/ unless left out; app/layout.py) and the app's own data (text/, state/, memory/, cards/),
plus research_brain-export.json (app version, schema, counts). Derived or disposable folders
(.index/, .trash/, exports/, backups/, .git/) are left out; the app rebuilds .index/ on start.
This is a full backup: private notes (app/privacy.py) are included, and so are state/privacy.json and the AI
audit log (state/ai_log.jsonl). Only reading-list exports (app/export.py) leave private notes out.

Import: only into a NEW, EMPTY vault (a new profile), never on top of an existing library, so nothing can
be overwritten. Every entry is checked before anything is written:
- no absolute paths, drive letters, backslashes, "..", NUL bytes, or symlinks (zip-slip);
- the resolved destination must stay inside the target folder;
- only known top-level folders are extracted (others are skipped and reported);
- caps on entry count, per-file and total uncompressed size, and compression ratio (zip bombs).
A zip whose files all sit under one top folder (e.g. "vault/notes/...") is accepted too, and so is a zip
from before the readable layout (notes/, pdfs/, topics/): the library is upgraded when it is first opened
(app/migrate.py). The documents of an imported library go to its documents folder (app/paths.py).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath

from . import __version__
from .layout import DOC_DIRS, PDFS_DIR, TYPE_DIRS

MANIFEST = "research_brain-export.json"
APP_DIRS = ("text", "state", "memory", "cards", "attachments")  # the library folder's own data
LEGACY_DIRS = ("notes", "pdfs", "topics")  # zips from before the readable layout
EXPORT_DIRS = (*DOC_DIRS, *APP_DIRS)
TOP_DIRS = (*EXPORT_DIRS, *LEGACY_DIRS)
NOTE_DIRS = (*TYPE_DIRS.values(), "notes")
# where this computer keeps things: meaningless on another one
LOCAL_STATE = ("papers.json", "library.json")
SKIP_DIRS = {".index", ".trash", "exports", "backups", ".git"}
MAX_ENTRIES = int(os.environ.get("RB_IMPORT_MAX_ENTRIES", "200000"))
MAX_TOTAL = int(os.environ.get("RB_IMPORT_MAX_GB", "50")) << 30
MAX_FILE = int(os.environ.get("RB_IMPORT_MAX_FILE_MB", "1024")) << 20
MAX_RATIO = 200  # uncompressed / compressed, per entry over 1 MB


class ZipImportError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------- export
def iter_files(vault: Path, include_pdfs: bool = True):
    from . import paths
    from .papers import REMOVED, folder_for
    vault = Path(vault)
    docs = paths.documents_dir(vault)
    for top in EXPORT_DIRS:
        if top == PDFS_DIR and not include_pdfs:
            continue
        # the papers folder may live elsewhere (app/papers.py); in the zip it is PDFs/
        base = folder_for(vault) if top == PDFS_DIR else (docs / top if top in DOC_DIRS else vault / top)
        if not base.is_dir():
            continue
        for root, dirs, files in os.walk(base):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")
                             and not (top == PDFS_DIR and d == REMOVED))
            for f in sorted(files):
                p = Path(root) / f
                if p.is_symlink() or f.endswith(".tmp") or f.startswith("."):
                    continue
                if top == "state" and f in LOCAL_STATE:
                    continue
                yield p, f"{top}/{p.relative_to(base).as_posix()}"


def export_zip(vault: Path, dest: Path, include_pdfs: bool = True) -> dict:
    """Write the library to `dest` (a .zip). Returns the manifest."""
    vault, dest = Path(vault), Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    counts = {"files": 0, "bytes": 0, "notes": 0, "pdfs": 0}
    schema = None
    try:
        schema = json.loads((vault / "state" / "meta.json").read_text(encoding="utf-8")).get("schema")
    except (OSError, ValueError):
        pass
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        for p, rel in iter_files(vault, include_pdfs):
            # PDFs are already compressed; storing them is much faster and barely bigger
            pdf = rel.startswith(f"{PDFS_DIR}/")
            z.write(p, rel, compress_type=zipfile.ZIP_STORED if pdf else zipfile.ZIP_DEFLATED)
            counts["files"] += 1
            counts["bytes"] += p.stat().st_size
            counts["notes"] += rel.split("/", 1)[0] in NOTE_DIRS and rel.endswith(".md")
            counts["pdfs"] += pdf
        manifest = {"format": "research_brain-vault", "format_version": 2, "app_version": __version__,
                    "schema": schema, "created": _now(), "include_pdfs": include_pdfs, **counts}
        z.writestr(MANIFEST, json.dumps(manifest, indent=1))
    os.replace(tmp, dest)
    return manifest


# ---------------------------------------------------------------- import
def _safe_name(name: str) -> PurePosixPath:
    if not name or "\x00" in name or "\\" in name:
        raise ZipImportError(f"Unsafe path in the zip: {name!r}")
    if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        raise ZipImportError(f"Absolute path in the zip: {name!r}")
    p = PurePosixPath(name)
    if any(part in ("..", "") for part in p.parts if part != ".") or ".." in p.parts:
        raise ZipImportError(f"Path escapes the folder: {name!r}")
    return PurePosixPath(*[x for x in p.parts if x != "."])


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return info.create_system == 3 and stat.S_ISLNK(info.external_attr >> 16)


def plan(zf: zipfile.ZipFile) -> tuple[list[tuple[zipfile.ZipInfo, PurePosixPath]], list[str], dict]:
    """Check every entry. Returns (entries to extract with their relative path, skipped names, manifest)."""
    infos = zf.infolist()
    if len(infos) > MAX_ENTRIES:
        raise ZipImportError(f"The zip has more than {MAX_ENTRIES} entries", 413)
    files = []
    for info in infos:
        rel = _safe_name(info.filename)
        if _is_symlink(info):
            raise ZipImportError(f"The zip contains a symbolic link ({info.filename}); refusing to import it")
        if info.is_dir():
            continue
        files.append((info, rel))
    # one wrapping folder ("vault/notes/a.md") is stripped
    tops = {r.parts[0] for _, r in files if len(r.parts) > 1}
    roots = {r.parts[0] for _, r in files}
    if len(roots) == 1 and len(tops) == 1 and next(iter(tops)) not in TOP_DIRS:
        files = [(i, PurePosixPath(*r.parts[1:])) for i, r in files if len(r.parts) > 1]
    manifest: dict = {}
    keep, skipped, total = [], [], 0
    for info, rel in files:
        if rel.as_posix() == MANIFEST:
            try:
                manifest = json.loads(zf.read(info)[:100_000])
            except ValueError:
                manifest = {}
            continue
        if len(rel.parts) < 2 or rel.parts[0] not in TOP_DIRS or any(p in SKIP_DIRS for p in rel.parts):
            skipped.append(info.filename)
            continue
        if info.file_size > MAX_FILE:
            raise ZipImportError(f"{info.filename} is larger than {MAX_FILE >> 20} MB", 413)
        if info.file_size > (1 << 20) and info.compress_size and info.file_size / info.compress_size > MAX_RATIO:
            raise ZipImportError(f"{info.filename} is compressed suspiciously well (possible zip bomb)", 413)
        total += info.file_size
        if total > MAX_TOTAL:
            raise ZipImportError(f"The zip unpacks to more than {MAX_TOTAL >> 30} GB", 413)
        keep.append((info, rel))
    if not any(r.parts[0] in NOTE_DIRS and r.suffix == ".md" for _, r in keep):
        raise ZipImportError("That zip doesn't contain a research_brain library (no papers, notes or questions)")
    return keep, skipped, manifest


def import_zip(src: Path, target: Path) -> dict:
    """Unpack the library zip `src` into `target`, which must not exist or be empty."""
    target = Path(target)
    if target.exists() and any(target.iterdir()):
        raise ZipImportError(f"{target} isn't empty; import into a new profile instead", 409)
    try:
        zf = zipfile.ZipFile(src)
    except zipfile.BadZipFile:
        raise ZipImportError("That file isn't a zip archive")
    with zf:
        keep, skipped, manifest = plan(zf)
        target.mkdir(parents=True, exist_ok=True)
        root = target.resolve()
        tmp = root.with_name(f".{root.name}.importing")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
        try:
            written = 0
            for info, rel in keep:
                dest = (tmp / Path(*rel.parts)).resolve()
                if tmp.resolve() not in dest.parents:
                    raise ZipImportError(f"Path escapes the folder: {info.filename!r}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as r, open(dest, "wb") as w:
                    n = 0
                    while chunk := r.read(1 << 20):
                        n += len(chunk)
                        if n > info.file_size or n > MAX_FILE:  # header lied about the size
                            raise ZipImportError(f"{info.filename} is larger than its header says", 413)
                        w.write(chunk)
                written += 1
            target.rmdir()
            tmp.replace(target)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
    _documents_out(target)
    from .profiles import _count_notes
    notes = _count_notes(target)
    return {"files": written, "notes": notes, "skipped": skipped[:50], "skipped_count": len(skipped),
            "manifest": manifest, "path": str(target)}


def _documents_out(target: Path) -> None:
    """Move an imported library's documents (Papers/, Notes/, ...) to its documents folder, when that is
    somewhere else (~/Documents/research brain (<profile>); app/paths.py)."""
    from . import paths
    docs = paths.documents_dir(target)
    if docs.resolve() == target.resolve() or not any((target / d).is_dir() for d in DOC_DIRS):
        return
    paths.remember_documents_dir(target, docs)
    for d in DOC_DIRS:
        src = target / d
        if not src.is_dir():
            continue
        dst = docs / d
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), dst)
            continue
        for p in sorted(src.rglob("*")):  # merge, never overwriting
            if p.is_file():
                t = dst / p.relative_to(src)
                t.parent.mkdir(parents=True, exist_ok=True)
                if not t.exists():
                    shutil.move(str(p), t)
        shutil.rmtree(src, ignore_errors=True)
