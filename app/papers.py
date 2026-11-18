"""Where your PDFs live, and keeping exactly one copy of each.

The papers folder. Every PDF in a library is one file, `<paper id>.pdf` (the id is the title's slug, so the
names read like the papers), in one folder: `<library>/pdfs/` unless you choose another one in Library
settings › Papers folder (for example ~/Documents/Research papers, or a synced folder). Choosing a folder
moves the files there; the choice is saved in the library's state/papers.json. Nothing else is ever written
there except `Removed from library/` (see below).

One copy each. Every stored PDF's SHA-256 is cached (app/acquire.py PdfHashes, .index/pdf_hashes.json).
Adding a PDF whose bytes match one you already have opens that paper instead of making a second, whichever
way it arrives (file picker, drag and drop, URL, the browser extension); a paper is also matched by DOI /
arXiv id as before. After you add a file through the
browser, the app looks for that exact file (same name and the same bytes) in your Downloads folder and
offers to move that copy to the Trash, since the library now has it. It looks at that one file name only.

Removing a paper asks what to do with its PDF:
    keep    the note goes to the library's .trash; the PDF moves to `<papers folder>/Removed from library/`
    trash   the note goes to the library's .trash; the PDF goes to your computer's Trash / Recycle Bin
            (restorable from there)

Checking a folder (opt-in, a folder you pick; nothing is scanned otherwise): lists the PDFs in it, says
which ones are already in the library (same bytes) and offers to move those copies to the Trash, and lets
you add the others, moving them into the papers folder (or copying them, if you prefer). Only files whose
bytes match a library PDF can be trashed through this, and only inside your home folder.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
from pathlib import Path

from . import fsio

REMOVED = "Removed from library"
CONFIG = "papers.json"
SCAN_LIMIT = 2000
_lock = threading.RLock()


# ---------------------------------------------------------------- the folder
def _state(root: Path) -> Path:
    d = Path(root) / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_folder(root: Path) -> Path:
    """Where a library keeps its PDFs unless you chose a folder: <documents>/PDFs (app/layout.py)."""
    from . import paths
    from .layout import PDFS_DIR
    return paths.documents_dir(root) / PDFS_DIR


def _config(root: Path) -> dict:
    try:
        cfg = json.loads((Path(root) / "state" / CONFIG).read_text(encoding="utf-8"))
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def online_only(root: Path) -> bool:
    """Keep new PDFs online only (a papers folder in OneDrive; app/diskspace.py)."""
    return bool(_config(root).get("online_only"))


def set_online_only(store, on: bool) -> dict:
    """Turn "keep new PDFs online only" on or off. Turning it on also asks the sync app to free the PDFs
    already there (Windows; on a Mac, Finder's Free up space does that)."""
    from . import diskspace
    with _lock:
        cfg = _config(store.root)
        cfg["online_only"] = bool(on)
        _write_json(_state(store.root) / CONFIG, cfg)
    freed = bool(on) and diskspace.keep_online_only(Path(store.pdf_dir), recursive=True)
    _log(store, f"papers folder: keep new PDFs online only {'on' if on else 'off'}")
    return {"online_only": bool(on), "freed": freed}


def folder_for(root: Path) -> Path:
    """The papers folder of the library at `root` (default <documents>/PDFs)."""
    try:
        cfg = json.loads((Path(root) / "state" / CONFIG).read_text(encoding="utf-8"))
        f = cfg.get("folder") if isinstance(cfg, dict) else None
        if f:
            return Path(f).expanduser()
    except (OSError, ValueError):
        pass
    return default_folder(root)


def _write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    fsio.replace(tmp, path)


def suggested_folder() -> Path:
    from .paths import documents_folder
    docs = documents_folder()
    return (docs if docs.is_dir() else Path.home()) / "Research papers"


def _library_pdfs(store, folder: Path) -> list[Path]:
    """The library's own PDFs in its papers folder: <paper id>.pdf for a paper in the library. Anything else
    there (PDFs you put in the folder yourself) isn't the library's, and is never moved."""
    if not folder.is_dir():
        return []
    out = []
    for p in sorted(folder.glob("*.pdf")):
        try:
            if store.exists(p.stem) and not store.is_linked(p.stem):
                out.append(p)
        except (ValueError, OSError):
            continue
    return out


def _dest(store, new: str) -> Path:
    """Where `new` points, checked: a full path, outside the library's own folder, and not one of (or around)
    the folders of papers you follow, whose PDFs research brain never moves."""
    dest = Path(new).expanduser()
    if not dest.is_absolute():
        raise ValueError("Give a full path, like ~/Documents/Research papers")
    dest = dest.resolve()
    root = Path(store.root).resolve()
    default = default_folder(store.root).resolve()
    if dest != default and (root in dest.parents or dest == root):
        raise ValueError("Pick a folder outside the library's own folder (or its PDFs folder).")
    name = followed_folder(store, dest)
    if name:
        raise ValueError(f"That's inside (or around) “{name}”, a folder of papers you follow. Its PDFs are already in "
                         "your library where they are, so the papers folder needs a place of its own: pick another folder.")
    return dest


def plan_move(store, new: str) -> dict:
    """What moving the papers folder to `new` would do, without doing it: which PDFs move (only the library's
    own), which stay (anything else in either folder), whether there's room, whether it's another disk."""
    dest = _dest(store, new)
    old = Path(store.pdf_dir)
    pdfs = _library_pdfs(store, old)
    size = sum(p.stat().st_size for p in pdfs)
    others = [p for p in old.glob("*.pdf")] if old.is_dir() else []
    others = [p.name for p in others if p not in pdfs]
    there = [p.name for p in dest.glob("*.pdf")] if dest.is_dir() else []
    clash = [p.name for p in pdfs if (dest / p.name).exists() and _sha(dest / p.name) != _sha(p)]
    try:
        probe = dest if dest.exists() else next(x for x in dest.parents if x.exists())
        other_disk = os.stat(probe).st_dev != os.stat(old).st_dev if old.is_dir() else False
    except (OSError, StopIteration):
        other_disk = False
    from . import diskspace
    room = True
    if other_disk:
        try:
            diskspace.ensure_room(dest, size, "these PDFs")
        except diskspace.DiskFull:
            room = False
    removed = len(list((old / REMOVED).glob("*.pdf"))) if (old / REMOVED).is_dir() else 0
    return {"from": str(old), "to": str(dest), "same": dest == old.resolve(), "moves": len(pdfs), "bytes": size,
            "removed": removed, "stay_behind": others[:20], "stay_behind_count": len(others),
            "already_there": there[:20], "already_there_count": len(there), "clash": clash[:5],
            "other_disk": other_disk, "room": room, "free": diskspace.free_bytes(dest),
            "cloud": diskspace.cloud_provider(dest)}


def move_folder(store, new: str) -> dict:
    """Move the library's own PDFs (one per paper, named after it) to `new` and use it from now on. Anything
    else in either folder is left exactly where it is. Refuses a folder holding different files with the same
    names, a folder inside the library's own state, a folder of papers you follow (or one around it), or a
    path that can't be created. plan_move() says beforehand what this will do."""
    dest = _dest(store, new)
    default = default_folder(store.root).resolve()
    with _lock, store.lock:
        old = Path(store.pdf_dir)
        if dest == old.resolve():
            return {"folder": str(dest), "moved": 0}
        dest.mkdir(parents=True, exist_ok=True)
        pdfs = _library_pdfs(store, old)
        clash = [p.name for p in pdfs if (dest / p.name).exists() and _sha(dest / p.name) != _sha(p)]
        if clash:
            raise ValueError(f"{dest} already has different files named like yours (e.g. {clash[0]}). Pick an empty folder.")
        try:
            other_disk = os.stat(dest).st_dev != os.stat(old).st_dev if old.is_dir() else False
        except OSError:
            other_disk = False
        if other_disk:  # moving to another disk copies every PDF: there must be room for all of them first
            from . import diskspace
            diskspace.ensure_room(dest, sum(p.stat().st_size for p in pdfs), f"your {len(pdfs)} PDFs")
        moved = 0
        for p in pdfs:
            target = dest / p.name
            if target.exists():  # the same bytes are already there
                p.unlink()
            else:
                shutil.move(str(p), target)
            moved += 1
        removed = old / REMOVED
        if removed.is_dir():
            (dest / REMOVED).mkdir(exist_ok=True)
            for p in removed.glob("*.pdf"):
                if not (dest / REMOVED / p.name).exists():
                    shutil.move(str(p), dest / REMOVED / p.name)
        _write_json(_state(store.root) / CONFIG, {**_config(store.root), "folder": "" if dest == default else str(dest)})
        store.pdf_dir = dest
        try:  # an emptied default folder can go; a chosen one stays
            if old.resolve() == default:
                (old / REMOVED).rmdir() if (old / REMOVED).is_dir() and not any((old / REMOVED).iterdir()) else None
        except OSError:
            pass
    _log(store, f"papers folder: {old} -> {dest} ({moved} PDFs)")
    return {"folder": str(dest), "moved": moved}


def _log(store, line: str) -> None:
    try:
        from . import paths
        import datetime as dt
        with open(paths.user_data_dir() / "storage.log", "a", encoding="utf-8", newline="\n") as f:
            f.write(f"{dt.datetime.now().isoformat(timespec='seconds')} {line}\n")
    except OSError:
        pass


def reveal(path: Path) -> None:
    """Open a folder in Finder / File Explorer / the file manager."""
    import subprocess
    import sys
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    elif os.name == "nt":
        os.startfile(str(path))  # noqa: S606 (a folder the app owns)
    else:
        subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------- one copy each
def _sha(path: Path) -> str:
    from .acquire import sha256_file
    return sha256_file(path)


def _by_sha(store) -> dict[str, str]:
    """{sha256: note id} of every stored PDF (app/acquire.py's cached index, re-hashing only changed files)."""
    from .acquire import PdfHashes
    return {sha: nid for nid, sha in PdfHashes(store).refresh().items()}


def find_by_hash(store, sha: str) -> str | None:
    nid = _by_sha(store).get(sha)
    return nid if nid and store.exists(nid) else None


# ---------------------------------------------------------------- removing a paper's PDF
def remove_pdf(store, nid: str, mode: str) -> dict:
    """Before deleting note `nid`: keep its PDF (moved to Removed from library/) or send it to the Trash. A PDF in
    one of your own folders of papers (app/linked.py) is never moved or deleted, whichever was asked."""
    if getattr(store, "is_linked", None) and store.is_linked(nid):
        return {"pdf": "left", "path": str(store.pdf_path(nid))}
    p = store.pdf_path(nid)
    if not p.is_file():
        return {"pdf": None}
    if mode == "keep":
        dest_dir = Path(store.pdf_dir) / REMOVED
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / p.name
        i = 2
        while dest.exists():
            dest = dest_dir / f"{p.stem} ({i}).pdf"
            i += 1
        shutil.move(str(p), dest)
        return {"pdf": "kept", "path": str(dest)}
    if mode == "trash":
        try:
            from send2trash import send2trash
            send2trash(str(p))
            where = "trash"
        except Exception:  # no system Trash (a server, a container): keep it restorable in the library's .trash
            return {"pdf": "library-trash"}
        return {"pdf": where}
    raise ValueError("pdf must be keep or trash")


# ---------------------------------------------------------------- copies elsewhere on the computer
def downloads_twin(filename: str, sha: str) -> str | None:
    """The same file (name and bytes) in ~/Downloads, if it's there: the copy the browser upload came from."""
    name = Path(filename or "").name
    if not name.lower().endswith(".pdf"):
        return None
    p = Path.home() / "Downloads" / name
    try:
        if p.is_file() and _sha(p) == sha:
            return str(p)
    except OSError:
        pass
    return None


def _inside_home(p: Path) -> bool:
    try:
        p.resolve().relative_to(Path.home().resolve())
        return True
    except ValueError:
        return False


def followed_folder(store, p: Path) -> str | None:
    """The folder of papers you follow (app/linked.py) that `p` is in or is, by its name; None if it's in none.
    research brain never moves or sends to the Trash anything in one: those PDFs are the library's own copies,
    kept where you keep them, and often in OneDrive, where a deletion reaches the cloud too."""
    try:
        rp = Path(p).expanduser().resolve()
    except OSError:
        rp = Path(p).expanduser()
    try:
        folders = store.links.folders()
    except Exception:
        return None
    for f in folders:
        root = Path(f.get("path") or "").expanduser()
        try:
            root = root.resolve()
        except OSError:
            pass
        if rp == root or root in rp.parents or rp in root.parents:
            return root.name or str(root)
    return None


def scan(store, folder: str, recursive: bool = False) -> dict:
    """The PDFs in a folder you picked: {"in_library": [{path, name, size, note}], "new": [{path, name, size}]}."""
    f = Path(folder).expanduser()
    if not f.is_dir():
        raise ValueError(f"{f} isn't a folder")
    if not _inside_home(f):
        raise ValueError("Pick a folder inside your home folder")
    if f.resolve() == Path(store.pdf_dir).resolve():
        raise ValueError("That's the papers folder itself")
    from .acquire import cloud_only
    by_sha = _by_sha(store)
    files = f.rglob("*.pdf") if recursive else f.glob("*.pdf")
    inl, new, followed, online = [], [], [], []
    i = -1
    for i, p in enumerate(files):
        if i >= SCAN_LIMIT:
            break
        if not p.is_file() or Path(store.pdf_dir) in p.parents:
            continue
        # a PDF in a folder of papers you follow is the library's own copy, kept there: not a spare copy, and
        # never offered for the Trash; it isn't even read here
        if followed_folder(store, p):
            followed.append({"path": str(p), "name": p.name})
            continue
        try:
            st = p.stat()
            if cloud_only(st):  # online only (OneDrive with its space freed): reading it would download it
                online.append({"path": str(p), "name": p.name, "size": st.st_size})
                continue
            sha, size = _sha(p), st.st_size
        except OSError:
            continue
        row = {"path": str(p), "name": p.name, "size": size}
        nid = by_sha.get(sha)
        if nid and store.exists(nid):
            inl.append({**row, "note": nid, "title": store.get(nid)["title"]})
        else:
            new.append(row)
    return {"folder": str(f), "in_library": inl, "new": new, "followed": followed, "online": online,
            "limited": i + 1 >= SCAN_LIMIT}


def trash_copies(store, paths: list[str]) -> dict:
    """Move to the Trash only files that are byte-for-byte copies of a PDF in the library, and never one in a
    folder of papers you follow (followed_folder). Only ever on your say-so: nothing calls this by itself."""
    from send2trash import send2trash
    by_sha = set(_by_sha(store))
    done, refused = [], []
    for s in paths[:SCAN_LIMIT]:
        p = Path(s).expanduser()
        try:
            if (not p.is_file() or not _inside_home(p) or Path(store.pdf_dir).resolve() in p.resolve().parents
                    or followed_folder(store, p) or _sha(p) not in by_sha):
                refused.append(s)
                continue
            send2trash(str(p))
            done.append(s)
        except Exception:
            refused.append(s)
    return {"trashed": done, "refused": refused}
