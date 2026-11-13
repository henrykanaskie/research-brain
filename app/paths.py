"""Where research_brain keeps your data, and which library (vault) this run uses.

Standard library only, so the backup script and the CLI can use it before any dependency is installed.

Data lives outside the app folder, in a per-user directory, so updating or reinstalling the app never
touches it:

    macOS    ~/Library/Application Support/research_brain/
    Linux    $XDG_DATA_HOME/research_brain/   (default ~/.local/share/research_brain/)
    Windows  %APPDATA%\\research_brain\\

    <data dir>/settings.json        which profile is active, optional custom profile paths
    <data dir>/profiles/<name>/     one vault per profile (notes/, pdfs/, text/, state/, ...)

Which vault a run uses, first match wins:

    1. an explicit vault: `--vault PATH` or RB_VAULT        (pinned: profile switching is off)
    2. an explicit profile: `--profile NAME` or RB_PROFILE
    3. settings.json "active_profile"
    4. "default": the legacy ./vault inside a source checkout, if it holds notes and the default
       profile has no notes yet (kept working in place; `research-brain move-legacy-vault` moves it),
       otherwise <data dir>/profiles/default.

RB_DATA_DIR overrides the data directory itself (tests use it; also handy for a portable install).
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "research_brain"
DEFAULT_PROFILE = "default"
PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")

PKG_DIR = Path(__file__).resolve().parent


def app_root() -> Path:
    """The folder holding static/, extension/ and scripts/: the repo root in a source checkout, the
    package folder itself when installed from a wheel (pyproject.toml copies them in)."""
    if (PKG_DIR.parent / "static" / "index.html").is_file():
        return PKG_DIR.parent
    return PKG_DIR


def is_source_checkout() -> bool:
    return app_root() == PKG_DIR.parent


def static_dir() -> Path:
    return app_root() / "static"


def legacy_vault() -> Path:
    """The pre-0.6 location: ./vault next to the app code (source checkouts only). RB_LEGACY_VAULT
    overrides it (tests)."""
    env = os.environ.get("RB_LEGACY_VAULT", "").strip()
    return Path(env).expanduser() if env else PKG_DIR.parent / "vault"


def user_data_dir() -> Path:
    env = os.environ.get("RB_DATA_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / APP_NAME
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(home / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    base = os.environ.get("XDG_DATA_HOME", "").strip() or str(home / ".local" / "share")
    return Path(base) / APP_NAME


def settings_path() -> Path:
    return user_data_dir() / "settings.json"


def load_settings() -> dict:
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        from . import fsio
        fsio.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_settings(updates: dict) -> dict:
    """Merge `updates` into settings.json (None deletes a key) and write it atomically."""
    data = load_settings()
    for k, v in updates.items():
        if v is None:
            data.pop(k, None)
        else:
            data[k] = v
    write_json_atomic(settings_path(), data)
    return data


def valid_profile(name: str) -> bool:
    return bool(PROFILE_RE.match(name or ""))


def _has_notes(vault: Path) -> bool:
    try:
        return any((vault / "notes").glob("*.md"))
    except OSError:
        return False


def profile_path(name: str, settings: dict | None = None) -> Path:
    """Where profile `name` lives: a custom path from settings["profile_paths"], else <data>/profiles/<name>.
    The legacy ./vault is the default profile's path until it is moved (see resolve())."""
    if not valid_profile(name):
        raise ValueError("Profile names use lowercase letters, digits, - and _ (at most 40 characters)")
    settings = load_settings() if settings is None else settings
    custom = (settings.get("profile_paths") or {}).get(name)
    if custom:
        return Path(custom).expanduser()
    std = user_data_dir() / "profiles" / name
    if name == DEFAULT_PROFILE and use_legacy(settings, std):
        return legacy_vault()
    return std


def use_legacy(settings: dict, std_default: Path) -> bool:
    """Keep using ./vault (pre-0.6 layout) when it holds notes and the default profile has none yet."""
    if settings.get("legacy_vault_moved") or not is_source_checkout():
        return False
    return _has_notes(legacy_vault()) and not _has_notes(std_default)


@dataclass
class Resolved:
    vault: Path
    profile: str            # profile name, or "" when the vault was given explicitly
    source: str             # "vault" (RB_VAULT/--vault), "profile" (RB_PROFILE/--profile), "settings", "default"
    pinned: bool = False    # an explicit vault: profiles can't be switched from the UI
    legacy: bool = False    # the pre-0.6 ./vault in the app folder
    notices: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"vault": str(self.vault), "profile": self.profile, "source": self.source, "pinned": self.pinned,
                "legacy": self.legacy, "notices": list(self.notices), "data_dir": str(user_data_dir())}


def resolve(vault: str | os.PathLike | None = None, profile: str | None = None) -> Resolved:
    """Decide which vault this run uses (see the module docstring for the order)."""
    explicit = str(vault) if vault else os.environ.get("RB_VAULT", "").strip()
    if explicit:
        return Resolved(Path(explicit).expanduser(), "", "vault", pinned=True)
    settings = load_settings()
    name, source = (profile or os.environ.get("RB_PROFILE", "").strip()), "profile"
    if not name:
        name, source = str(settings.get("active_profile") or ""), "settings"
    if not name:
        name, source = DEFAULT_PROFILE, "default"
    notices: list[str] = []
    if not valid_profile(name):
        notices.append(f"Ignored invalid profile name {name!r}; using {DEFAULT_PROFILE!r}.")
        name = DEFAULT_PROFILE
    path = profile_path(name, settings)
    legacy = name == DEFAULT_PROFILE and path == legacy_vault()
    if legacy:
        notices.append(f"Using your library in the app folder ({path}). It keeps working there; to move it to "
                       f"{user_data_dir() / 'profiles' / DEFAULT_PROFILE} (safe from app updates), stop the app "
                       "and run: research-brain move-legacy-vault")
    return Resolved(path, name, source, legacy=legacy, notices=notices)


# ---------------------------------------------------------------- the documents folder
LIBRARY_CONFIG = "library.json"   # <library>/state/library.json: {"documents": "<folder>"}
DOCS_MARKER = ".research-brain.json"  # in the documents folder: which library it belongs to


def default_documents_dir(vault: Path) -> Path:
    """Where a library's documents (papers, notes, questions, highlights, topics, PDFs; app/layout.py) go
    when nothing was chosen: ~/Documents/research brain for a profile in the normal data folder ("research
    brain (<profile>)" for other profiles), so they are easy to find; the library folder itself otherwise
    (RB_DATA_DIR, RB_VAULT, a profile in a folder you chose, tests). RB_DOCUMENTS_DIR picks one outright."""
    env = os.environ.get("RB_DOCUMENTS_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    vault = Path(vault)
    docs = Path.home() / "Documents"
    managed = not os.environ.get("RB_DATA_DIR", "").strip() and not os.environ.get("RB_VAULT", "").strip()
    try:
        managed = managed and vault.resolve().parent == (user_data_dir() / "profiles").resolve()
    except OSError:
        managed = False
    if not managed or not docs.is_dir():
        return vault
    base = docs / ("research brain" if vault.name == DEFAULT_PROFILE else f"research brain ({vault.name})")
    cand, i = base, 2
    while _taken(cand, vault):  # a folder of that name that isn't this library's: pick another name
        cand, i = base.with_name(f"{base.name} {i}"), i + 1
    return cand


def _taken(folder: Path, vault: Path) -> bool:
    if not folder.exists():
        return False
    try:
        mark = json.loads((folder / DOCS_MARKER).read_text(encoding="utf-8"))
        return Path(mark.get("library", "")).resolve() != Path(vault).resolve()
    except (OSError, ValueError, AttributeError):
        return any(folder.iterdir())  # an existing folder with things in it, not made by research brain


def documents_dir(vault: Path) -> Path:
    """The documents folder of the library at `vault`: as recorded in state/library.json, else the default."""
    try:
        d = json.loads((Path(vault) / "state" / LIBRARY_CONFIG).read_text(encoding="utf-8")).get("documents")
        if d:
            return Path(d).expanduser()
    except (OSError, ValueError, AttributeError):
        pass
    return default_documents_dir(vault)


def remember_documents_dir(vault: Path, docs: Path) -> None:
    """Record the documents folder (so it never changes behind the user's back) and mark it as this
    library's."""
    vault, docs = Path(vault), Path(docs)
    cfg = vault / "state" / LIBRARY_CONFIG
    try:
        cur = json.loads(cfg.read_text(encoding="utf-8")).get("documents")
    except (OSError, ValueError, AttributeError):
        cur = None
    if cur != str(docs):
        write_json_atomic(cfg, {"documents": str(docs)})
    if docs.resolve() != vault.resolve():
        docs.mkdir(parents=True, exist_ok=True)
        mark = docs / DOCS_MARKER
        if not mark.exists():
            write_json_atomic(mark, {"library": str(vault), "what": "research brain's papers, notes, questions "
                                     "and highlights. The app's own data is in the library folder named here."})


# ---------------------------------------------------------------- storage log
def log(message: str) -> None:
    """Append one line to <data dir>/storage.log (vault moves, migrations, imports). Never raises."""
    import datetime as _dt
    try:
        d = user_data_dir()
        d.mkdir(parents=True, exist_ok=True)
        stamp = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
        with (d / "storage.log").open("a", encoding="utf-8", newline="\n") as f:
            f.write(f"{stamp} {message}\n")
    except OSError:
        pass


# ---------------------------------------------------------------- moving the legacy ./vault
class MoveError(RuntimeError):
    pass


def _tree_stats(root: Path) -> tuple[int, int]:
    files = size = 0
    for p in root.rglob("*"):
        if p.is_file() and not p.is_symlink():
            files += 1
            size += p.stat().st_size
    return files, size


def move_legacy_vault() -> Path:
    """Move the pre-0.6 ./vault into <data dir>/profiles/default, safely:

    1. copy it to a temporary folder next to the destination,
    2. check the copy has the same number of files and bytes,
    3. rename the copy into place,
    4. rename the old folder to vault.moved-<timestamp> (kept, not deleted; remove it yourself later),
    5. record legacy_vault_moved in settings.json and log it.
    The server must not be running (the CLI checks the port first). Returns the new vault path."""
    import datetime as _dt
    import shutil
    src = legacy_vault()
    dst = user_data_dir() / "profiles" / DEFAULT_PROFILE
    if not _has_notes(src):
        raise MoveError(f"There is no library with notes at {src} to move.")
    if dst.exists():
        if _tree_stats(dst)[0]:
            raise MoveError(f"{dst} already holds files; not overwriting it. Move or rename it first.")
        shutil.rmtree(dst)  # only empty folders
    tmp = dst.with_name(f".{dst.name}.moving")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, tmp, symlinks=True)
    if _tree_stats(src) != _tree_stats(tmp):
        shutil.rmtree(tmp, ignore_errors=True)
        raise MoveError("The copy doesn't match the original (file count or size differs); nothing was changed.")
    tmp.replace(dst)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    old = src.with_name(f"vault.moved-{stamp}")
    src.replace(old)
    save_settings({"legacy_vault_moved": str(dst), "move_legacy_on_start": None})
    log(f"moved legacy vault {src} -> {dst} (old copy kept at {old})")
    return dst


if __name__ == "__main__":  # used by scripts/vault_backup.sh:  python3 app/paths.py [--documents VAULT]
    if len(sys.argv) == 3 and sys.argv[1] == "--documents":
        print(documents_dir(Path(sys.argv[2])))
    else:
        print(resolve().vault)
