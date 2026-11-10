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

SCHEMA_VERSION = 1
META = "meta.json"
# Never copied into a pre-migration backup: big and untouched by migrations (pdfs/), derived (.index/),
# or not part of the library (.trash/, exports/, backups/, a git snapshot repo).
BACKUP_SKIP = {"pdfs", ".index", ".trash", "exports", "backups", ".git"}


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
    for sub in ("notes", "state", "memory", "topics"):
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


# to_version -> (description, function(vault)). Add new ones at the end; never edit a shipped one.
MIGRATIONS: dict[int, tuple[str, Callable[[Path], None]]] = {
    1: ("move review/engagement state from .index/ to state/", _m1_state_files),
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
                {"from": v - 1, "to": v, "at": _now(), "what": desc, "backup": str(bk.relative_to(vault))})
            meta.update(schema=v, app_version=__version__)
            _write_meta(vault, meta)  # after each step, so an interrupted run resumes where it stopped
        return {"from": have, "to": target, "backup": str(bk), "applied": applied}
