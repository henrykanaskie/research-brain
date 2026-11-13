"""Vault schema version and migrations.

Every vault records the layout version it was written with in state/meta.json:

    {"schema": 1, "app_version": "0.6.0", "created": "...", "migrations": [{"from": 0, "to": 1, "at": "...",
     "backup": "backups/pre-migration-v0-to-v1-20261110-101500.zip"}]}

On startup the server calls run(vault):

- a brand-new (empty) vault just gets meta.json at the current SCHEMA_VERSION;
- a vault without meta.json but with notes is version 0 (written before versioning existed);
- an older version is migrated one step at a time, after a zip backup of everything a migration can
  touch (all of the vault except pdfs/, which no migration rewrites, and derived or disposable folders);
- a NEWER version than this app knows stops the server with a clear message (an older app must never
  rewrite a library a newer one has upgraded).

A file lock (state/.migrate.lock) keeps two processes from migrating at once. Migrations must be
idempotent, so a run interrupted halfway (power loss) can simply run again.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import zipfile
from pathlib import Path
from typing import Callable

from . import __version__
from . import paths

SCHEMA_VERSION = 2
META = "meta.json"
# Never copied into a pre-migration backup: big and untouched by migrations (pdfs/), derived (.index/),
# or not part of the library (.trash/, exports/, backups/, a git snapshot repo).
BACKUP_SKIP = {"pdfs", "PDFs", ".index", ".trash", "exports", "backups", ".git"}


class SchemaError(RuntimeError):
    """The vault was written by a newer research_brain, or its meta.json is unusable."""


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def meta_path(vault: Path) -> Path:
    return Path(vault) / "state" / META


def read_meta(vault: Path) -> dict | None:
    p = meta_path(vault)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SchemaError(f"{p} can't be read ({e}). Fix or remove it; the notes themselves are untouched.")
    if not isinstance(data, dict) or not isinstance(data.get("schema"), int):
        raise SchemaError(f"{p} has no valid \"schema\" number.")
    return data


def _has_content(vault: Path) -> bool:
    for sub in ("notes", "state", "memory", "topics", "Papers", "Notes", "Questions", "Highlights", "Topics"):
        d = Path(vault) / sub
        try:
            if d.is_dir() and any(not p.name.startswith(".") and p.name != META for p in d.iterdir()):
                return True
        except OSError:
            pass
    return False


def current_version(vault: Path) -> int:
    m = read_meta(vault)
    if m is not None:
        return m["schema"]
    return 0 if _has_content(vault) else SCHEMA_VERSION


# ---------------------------------------------------------------- migrations
def _m1_state_files(vault: Path) -> None:
    """v0 -> v1: per-user state (review schedule, open counts) lives in state/, not the derived .index/."""
    state = vault / "state"
    state.mkdir(parents=True, exist_ok=True)
    for name in ("review.json", "engagement.json"):
        old, new = vault / ".index" / name, state / name
        if old.exists() and not new.exists():
            old.replace(new)


def _front(raw: str) -> dict:
    """A note file's frontmatter as a dict ({} when missing or unreadable)."""
    from .store import load_frontmatter
    raw = raw.replace("\r\n", "\n")
    if not raw.startswith("---\n"):
        return {}
    end = raw.find("\n---\n", 3)
    if end == -1:
        return {}
    try:
        data = load_frontmatter(raw[4:end + 1])
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _with_id(raw: str, nid: str) -> str:
    """The file's text with `id: <nid>` as the first property; everything else exactly as it was."""
    nl = "\r\n" if raw.startswith("---\r\n") else "\n"
    if raw.startswith("---" + nl) and raw.find(nl + "---", 3) != -1:
        import re
        head = raw[:raw.find(nl + "---", 3)]
        if re.search(r"^id:", head, re.M):
            return raw if _id_in(raw) == nid else re.sub(r"^id:.*$", f"id: {nid}", raw, count=1, flags=re.M)
        return raw[:3 + len(nl)] + f"id: {nid}{nl}" + raw[3 + len(nl):]
    return f"---\nid: {nid}\n---\n" + raw


def _id_in(raw: str) -> str | None:
    import re
    m = re.search(r"^id:[ \t]*['\"]?([a-z0-9][a-z0-9-]*)", raw.replace("\r\n", "\n").split("\n---", 2)[0], re.M)
    return m.group(1) if m else None


def _move_tree(src: Path, dst: Path) -> None:
    """Move every file under src into dst (same relative paths), never overwriting: a name already taken
    by different bytes gets " (2)"; the same bytes there already just drops the source copy."""
    import filecmp
    import shutil
    if not src.is_dir():
        return
    for p in sorted(src.rglob("*")):
        if not p.is_file() or p.is_symlink():
            continue
        t = dst / p.relative_to(src)
        t.parent.mkdir(parents=True, exist_ok=True)
        if t.exists():
            if os.path.samefile(p, t):
                continue  # one file under two names (a case-insensitive disk): never delete it
            if filecmp.cmp(p, t, shallow=False):
                p.unlink()
                continue
            i = 2
            while t.exists():
                t = t.with_name(f"{p.stem} ({i}){p.suffix}")
                i += 1
        shutil.move(str(p), t)
    for d in sorted((d for d in src.rglob("*") if d.is_dir()), key=lambda d: len(d.parts), reverse=True):
        try:
            d.rmdir()
        except OSError:
            pass
    try:
        src.rmdir()
    except OSError:
        pass


def _m2_readable_layout(vault: Path) -> None:
    """v1 -> v2: papers, notes, questions and highlights become readable files in folders by type, in the
    library's documents folder (~/Documents/research brain for a normal install; app/layout.py,
    app/paths.py), each with its id written in it; topics go to Topics/ and the PDFs (unless you chose a
    papers folder) to PDFs/. Every file keeps its exact text apart from the added id line; a file is
    removed from notes/ only after its copy is written and read back. Safe to run again after a stop."""
    from .layout import PDFS_DIR, TOPICS_DIR, TYPE_DIRS, Layout
    from .papers import CONFIG
    vault = Path(vault)
    # First give the old folders names no new one can have. On a case-insensitive disk (macOS, Windows)
    # notes/ and Notes/, topics/ and Topics/, pdfs/ and PDFs/ are the same folder; renamed, they can't be
    # mistaken for the new ones. A run that stopped halfway finds them under these names.
    try:
        chosen = json.loads((vault / "state" / CONFIG).read_text(encoding="utf-8")).get("folder")
    except (OSError, ValueError, AttributeError):
        chosen = None
    moves = [("notes", ".v1-notes"), ("topics", ".v1-topics")] + ([] if chosen else [("pdfs", ".v1-pdfs")])
    for name, aside in moves:
        src, dst = vault / name, vault / aside
        if src.is_dir() and not dst.exists() and not _is_new_folder(vault, name):
            src.replace(dst)
    docs = paths.documents_dir(vault)
    paths.remember_documents_dir(vault, docs)
    lay = Layout(docs)
    lay.ensure()
    lay.scan()
    old = vault / ".v1-notes"
    if old.is_dir():
        entries = []
        for f in sorted(old.glob("*.md")):
            try:
                raw = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            meta = _front(raw)
            typ = meta.get("type") if meta.get("type") in TYPE_DIRS else "note"
            entries.append((f, f.stem, raw, meta, typ))
        rank = {"paper": 0, "note": 1, "question": 1, "fragment": 2}  # papers first: highlights go in their folders
        for f, nid, raw, meta, typ in sorted(entries, key=lambda e: (rank[e[4]], e[1])):
            text = _with_id(raw, nid)
            done = lay.current(nid)  # map only: every file this run writes is remembered
            if done is not None and os.path.samefile(done, f):
                done = None  # never take the original for its copy
            if done is not None:  # copied by an earlier run that stopped before removing the original
                if done.read_text(encoding="utf-8") == text:
                    f.unlink()
                    continue
            note = {"id": nid, "type": typ, "title": str(meta.get("title") or nid), "year": meta.get("year"),
                    "parent": str(meta.get("parent") or ""), "quote": str(meta.get("quote") or "")}
            target = done or lay.target(note)
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(f".{target.name}.tmp")
            tmp.write_text(text, encoding="utf-8", newline="")
            os.replace(tmp, target)
            if target.read_text(encoding="utf-8") != text:
                raise RuntimeError(f"Copying {f} to {target} didn't give the same text; stopped, nothing removed.")
            lay.remember(nid, target)
            f.unlink()
        try:
            old.rmdir()  # anything else left in notes/ stays where it is
        except OSError:
            pass
    _move_tree(vault / ".v1-topics", docs / TOPICS_DIR)
    if not chosen:  # the default papers folder moves with the documents; a folder you chose stays
        _move_tree(vault / ".v1-pdfs", docs / PDFS_DIR)
    # anything else that was in notes/ (an image, a file from another app) goes to Notes/, never overwriting
    _move_tree(vault / ".v1-notes", docs / TYPE_DIRS["note"])


def _is_new_folder(vault: Path, name: str) -> bool:
    """`name` (notes, topics, pdfs) is really the new-layout folder (Notes, Topics, PDFs) already: on a
    case-insensitive disk after a finished migration. Its real name on disk says which."""
    try:
        return name not in os.listdir(vault)
    except OSError:
        return False


# to_version -> (description, function(vault)). Add new ones at the end; never edit a shipped one.
MIGRATIONS: dict[int, tuple[str, Callable[[Path], None]]] = {
    1: ("move review/engagement state from .index/ to state/", _m1_state_files),
    2: ("papers, notes, questions and highlights as readable files in folders (Papers/, Notes/, Questions/, "
        "Highlights/, Topics/, PDFs/)", _m2_readable_layout),
}


def backup(vault: Path, label: str) -> Path:
    """Zip everything a migration may touch into <vault>/backups/<label>-<stamp>.zip."""
    vault = Path(vault)
    dest_dir = vault / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = dest_dir / f"{label}-{stamp}.zip"
    tmp = dest.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(vault):
            rel_root = Path(root).relative_to(vault)
            if rel_root == Path("."):
                dirs[:] = [d for d in dirs if d not in BACKUP_SKIP]
            for f in files:
                p = Path(root) / f
                if p.is_symlink() or f.endswith(".tmp"):
                    continue
                z.write(p, str(rel_root / f) if rel_root != Path(".") else f)
        docs = paths.documents_dir(vault)  # the documents folder, when it is elsewhere (app/layout.py)
        if docs.is_dir() and docs.resolve() != vault.resolve():
            for root, dirs, files in os.walk(docs):
                rel_root = Path(root).relative_to(docs)
                if rel_root == Path("."):
                    dirs[:] = [d for d in dirs if d not in BACKUP_SKIP]
                for f in files:
                    p = Path(root) / f
                    if not p.is_symlink() and not f.endswith(".tmp"):
                        z.write(p, str(Path("documents") / rel_root / f))
    os.replace(tmp, dest)
    return dest


class _Lock:
    def __init__(self, path: Path):
        self.path, self.f = path, None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(self.path, "a+")
        try:
            import fcntl
            fcntl.flock(self.f.fileno(), fcntl.LOCK_EX)
        except ImportError:  # Windows: one process per vault is the normal case anyway
            pass
        return self

    def __exit__(self, *exc):
        try:
            import fcntl
            fcntl.flock(self.f.fileno(), fcntl.LOCK_UN)
        except ImportError:
            pass
        self.f.close()


def _write_meta(vault: Path, meta: dict) -> None:
    paths.write_json_atomic(meta_path(vault), meta)


def run(vault: Path, *, target: int = SCHEMA_VERSION, log: Callable[[str], None] | None = None) -> dict:
    """Bring `vault` to `target`. Returns {"from", "to", "backup", "applied"}. Raises SchemaError for a vault
    newer than this app."""
    vault = Path(vault)
    say = log or (lambda m: print(f"[research_brain] {m}"))
    vault.mkdir(parents=True, exist_ok=True)
    with _Lock(vault / "state" / ".migrate.lock"):
        meta = read_meta(vault)
        have = meta["schema"] if meta else (0 if _has_content(vault) else None)
        if have is None:  # new, empty vault
            _write_meta(vault, {"schema": target, "app_version": __version__, "created": _now(), "migrations": []})
            return {"from": target, "to": target, "backup": None, "applied": []}
        if have > target:
            raise SchemaError(
                f"This library ({vault}) was last opened by a newer research_brain (schema {have}; this app "
                f"understands up to {target}). Update research_brain; nothing was changed.")
        meta = dict(meta or {"created": _now(), "migrations": []})
        if have == target:
            if meta.get("app_version") != __version__ or meta.get("schema") != target:
                meta.update(schema=target, app_version=__version__)
                _write_meta(vault, meta)
            return {"from": have, "to": have, "backup": None, "applied": []}
        bk = backup(vault, f"pre-migration-v{have}-to-v{target}")
        say(f"Upgrading library {vault} from schema {have} to {target}; backup: {bk}")
        paths.log(f"migrating {vault} schema {have} -> {target}; backup {bk}")
        applied = []
        for v in range(have + 1, target + 1):
            desc, fn = MIGRATIONS[v]
            fn(vault)
            applied.append(v)
            meta.setdefault("migrations", []).append(
                {"from": v - 1, "to": v, "at": _now(), "what": desc, "backup": bk.relative_to(vault).as_posix()})
            meta.update(schema=v, app_version=__version__)
            _write_meta(vault, meta)  # after each step, so an interrupted run resumes where it stopped
        return {"from": have, "to": target, "backup": str(bk), "applied": applied}
