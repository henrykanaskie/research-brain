"""Is there a newer research_brain, and how do I install it?

Releases are published by `.github/workflows/release.yml` when a vX.Y.Z tag is pushed: a wheel, the
install scripts, constraints.txt, the browser extension as a zip, and `latest.json`:

    {"version": "0.7.0", "wheel": "research_brain-0.7.0-py3-none-any.whl", "notes": "...", "published": "..."}

The app looks for that file in its *update source*, in this order:

1. RB_UPDATE_SOURCE, or the "update_source" setting (Settings → Updates): a folder (the lab's shared drive,
   OneDrive, Box, a network share: anything synced to disk) holding latest.json and the wheels, or an
   https URL of a latest.json. This is the lab channel, and it needs no GitHub account.
2. Otherwise the GitHub repository's latest release, which needs read access to the (private) repo: a
   GITHUB_TOKEN, or the `gh` command signed in. Without either, the check quietly finds nothing.

It checks at most once a day (cached in the data folder) and never blocks the app: the server asks in a
background thread, and the result only adds a small "Update available" note. RB_UPDATE_CHECK=0 turns it
off. `research-brain update` installs the new version with whatever installed this one (uv, pipx or the
private virtualenv from install.sh); a source checkout is told to `git pull` instead.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import requests

from . import __version__, paths

REPO = "henrykanaskie/research-brain"
CHECK_EVERY = dt.timedelta(hours=24)
_VER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[.-]?(?:post|rc|a|b|dev)?\d*)?$")


def parse(v: str) -> tuple[int, int, int] | None:
    m = _VER.match((v or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def newer(latest: str, current: str = __version__) -> bool:
    a, b = parse(latest), parse(current)
    return bool(a and b and a > b)


def enabled() -> bool:
    if os.environ.get("RB_UPDATE_CHECK", "").strip() == "0":
        return False
    return os.environ.get("RB_METADATA", "").strip() != "0"  # the test suite and offline use


def source() -> str:
    env = os.environ.get("RB_UPDATE_SOURCE", "").strip()
    if env:
        return env
    v = paths.load_settings().get("update_source")
    return v.strip() if isinstance(v, str) else ""


def set_source(value: str | None) -> str:
    value = (value or "").strip()
    if value and not value.startswith("https://"):
        p = Path(value).expanduser()
        if not p.exists():
            raise ValueError(f"{p} doesn't exist. Use the folder that holds latest.json, or an https URL.")
        value = str(p.resolve())
    paths.save_settings({"update_source": value or None})
    return value


def _cache_path() -> Path:
    return paths.user_data_dir() / "update-check.json"


def _from_folder(folder: Path) -> dict | None:
    lj = folder / "latest.json" if folder.is_dir() else folder
    if not lj.is_file():
        return None
    info = json.loads(lj.read_text(encoding="utf-8"))
    wheel = info.get("wheel") or ""
    if wheel and not re.match(r"^[a-z]+://", wheel):
        info["wheel"] = str((lj.parent / wheel).resolve())
    info["source"] = str(lj.parent)
    return info


def _github_token() -> str:
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if tok or not shutil.which("gh"):
        return tok
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _from_github() -> dict | None:
    tok = _github_token()
    if not tok:
        return None  # a private repo answers 404 without one: don't bother asking
    h = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {tok}", "X-GitHub-Api-Version": "2022-11-28"}
    r = requests.get(f"https://api.github.com/repos/{REPO}/releases/latest", headers=h, timeout=6)
    if r.status_code != 200:
        return None
    rel = r.json()
    wheel = next((a for a in rel.get("assets") or [] if a.get("name", "").endswith(".whl")), None)
    return {"version": (rel.get("tag_name") or "").lstrip("v"), "notes": rel.get("body") or "",
            "published": rel.get("published_at") or "", "url": rel.get("html_url") or "",
            # the API's asset URL needs the token, so the installer uses the tag instead
            "wheel": "", "tag": rel.get("tag_name") or "", "has_wheel": bool(wheel), "source": "github"}


def fetch() -> dict | None:
    """The newest release's latest.json from the update source, or None. Raises nothing."""
    src = source()
    try:
        if src.startswith("https://"):
            r = requests.get(src, timeout=6)
            if r.status_code != 200:
                return None
            info = r.json()
            base = src.rsplit("/", 1)[0]
            if info.get("wheel") and "://" not in info["wheel"]:
                info["wheel"] = f"{base}/{info['wheel']}"
            info["source"] = src
            return info
        if src:
            return _from_folder(Path(src).expanduser())
        return _from_github()
    except (OSError, ValueError, requests.RequestException):
        return None


def check(force: bool = False) -> dict:
    """{"current", "latest", "newer", "notes", "url", "source", "checked", "command"}; cached for a day."""
    out = {"current": __version__, "latest": None, "newer": False, "notes": "", "url": "", "source": source() or "github",
           "checked": None, "command": "research-brain update", "enabled": enabled()}
    if not enabled():
        return out
    now = dt.datetime.now(dt.timezone.utc)
    cached = None
    try:
        cached = json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if cached and not force and cached.get("source") == out["source"]:
        try:
            if now - dt.datetime.fromisoformat(cached["checked"]) < CHECK_EVERY:
                info = cached.get("info")
                return _merge(out, info, cached["checked"])
        except (KeyError, ValueError, TypeError):
            pass
    info = fetch()
    stamp = now.isoformat(timespec="seconds")
    try:
        paths.write_json_atomic(_cache_path(), {"checked": stamp, "source": out["source"], "info": info})
    except OSError:
        pass
    return _merge(out, info, stamp)


def _merge(out: dict, info: dict | None, stamp: str) -> dict:
    out["checked"] = stamp
    if info and parse(str(info.get("version", ""))):
        out.update(latest=info["version"], newer=newer(info["version"]), notes=(info.get("notes") or "")[:2000],
                   url=info.get("url") or "", published=info.get("published") or "")
    return out


# ---------------------------------------------------------------- installing
def how_installed() -> str:
    """"checkout", "uv", "pipx", "venv" (install.sh's private virtualenv) or "pip"."""
    if (paths.PKG_DIR.parent / ".git").exists() and (paths.PKG_DIR.parent / "pyproject.toml").exists():
        return "checkout"
    env = Path(sys.prefix)
    prefix = env.as_posix().lower()
    if (env / "uv-receipt.toml").is_file() or "/uv/tools/" in prefix:  # uv's record of a `uv tool install`
        return "uv"
    if (env / "pipx_metadata.json").is_file() or "/pipx/venvs/" in prefix:
        return "pipx"
    if prefix.endswith("/app-venv"):
        return "venv"
    return "pip"


def install_spec(info: dict, extras: str = "") -> str:
    """What to hand the installer: the release's wheel (a path or URL), else the git tag."""
    wheel = info.get("wheel") or ""
    name = f"research-brain{extras}"
    if wheel:
        if extras:
            uri = Path(wheel).as_uri() if "://" not in wheel else wheel
            return f"{name} @ {uri}"
        return wheel
    tag = info.get("tag") or f"v{info['version']}"
    return f"{name} @ git+https://github.com/{REPO}.git@{tag}"


def install_command(info: dict, how: str | None = None) -> list[str] | None:
    how = how or how_installed()
    spec = install_spec(info, "[embeddings]" if _has_embeddings() else "")
    cons = []
    folder = info.get("source") or ""
    pinned_ok = how == "uv" or sys.version_info >= (3, 12)  # constraints.txt pins wheels built for 3.12+
    if pinned_ok and folder and Path(folder).is_dir() and (Path(folder) / "constraints.txt").is_file():
        cons = ["-c", str(Path(folder) / "constraints.txt")]
    if how == "uv":
        return ["uv", "tool", "install", "--force", "--python", "3.12", *cons, spec]
    if how == "pipx":
        return ["pipx", "install", "--force", *(["--pip-args", f"-c {cons[1]}"] if cons else []), spec]
    if how in ("venv", "pip"):
        return [sys.executable, "-m", "pip", "install", "--upgrade", *cons, spec]
    return None


def _has_embeddings() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("sentence_transformers") is not None
    except (ImportError, ValueError):
        return False
