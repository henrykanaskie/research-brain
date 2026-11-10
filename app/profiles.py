"""Profiles: separate libraries (vaults) on one computer, e.g. "thesis" and "teaching", or two people
sharing a lab machine. A profile is only a folder; there are no accounts or passwords (the computer's
own user account is the identity, see docs/storage-and-distribution.md).

    <data dir>/profiles/<name>/      one vault per profile
    <data dir>/settings.json         {"active_profile": "thesis", "profile_paths": {"shared": "/Volumes/x"}}

The running server serves one vault. Switching writes "active_profile" and restarts the server process
(app/cli.py supervises it); without the supervisor (plain uvicorn) the switch applies on the next start.
"""
from __future__ import annotations

from pathlib import Path

from . import paths


class ProfileError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _count_notes(vault: Path) -> int:
    try:
        return sum(1 for _ in (vault / "notes").glob("*.md"))
    except OSError:
        return 0


def names(settings: dict | None = None) -> list[str]:
    settings = paths.load_settings() if settings is None else settings
    found = {paths.DEFAULT_PROFILE}
    root = paths.user_data_dir() / "profiles"
    try:
        for d in root.iterdir():
            if d.is_dir() and paths.valid_profile(d.name):
                found.add(d.name)
    except OSError:
        pass
    for n in (settings.get("profile_paths") or {}):
        if paths.valid_profile(n):
            found.add(n)
    return sorted(found, key=lambda n: (n != paths.DEFAULT_PROFILE, n))


def list_profiles(current: "paths.Resolved | None" = None) -> dict:
    settings = paths.load_settings()
    selected = str(settings.get("active_profile") or paths.DEFAULT_PROFILE)
    out = []
    for n in names(settings):
        p = paths.profile_path(n, settings)
        out.append({"name": n, "path": str(p), "exists": p.exists(), "notes": _count_notes(p),
                    "current": bool(current and not current.pinned and current.profile == n),
                    "selected": n == selected,
                    "legacy": n == paths.DEFAULT_PROFILE and p == paths.legacy_vault()})
    return {"profiles": out, "current": current.profile if current else "", "pinned": bool(current and current.pinned),
            "vault": str(current.vault) if current else "", "data_dir": str(paths.user_data_dir())}


def create(name: str, path: str | None = None) -> dict:
    name = (name or "").strip().lower()
    if not paths.valid_profile(name):
        raise ProfileError("Profile names use lowercase letters, digits, - and _ (at most 40 characters)")
    settings = paths.load_settings()
    if name in names(settings) and paths.profile_path(name, settings).exists():
        raise ProfileError(f"A profile named {name!r} already exists", 409)
    if path:
        p = Path(path).expanduser().resolve()
        if p.exists() and any(p.iterdir()) and not (p / "notes").is_dir():
            raise ProfileError(f"{p} isn't empty and doesn't look like a research_brain library", 409)
        custom = dict(settings.get("profile_paths") or {})
        custom[name] = str(p)
        paths.save_settings({"profile_paths": custom})
    else:
        p = paths.user_data_dir() / "profiles" / name
    for sub in ("notes", "pdfs", "text", "state"):
        (p / sub).mkdir(parents=True, exist_ok=True)
    from . import migrate
    migrate.run(p, log=lambda m: None)  # stamps state/meta.json
    paths.log(f"created profile {name} at {p}")
    return {"name": name, "path": str(p)}


def select(name: str) -> dict:
    """Make `name` the profile used from the next (re)start on."""
    settings = paths.load_settings()
    if name not in names(settings):
        raise ProfileError(f"No profile named {name!r}", 404)
    paths.save_settings({"active_profile": name})
    paths.log(f"selected profile {name}")
    return {"name": name, "path": str(paths.profile_path(name))}
