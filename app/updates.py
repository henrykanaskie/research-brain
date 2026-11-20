"""Is there a newer research_brain, and how do I install it?

Releases are published by `.github/workflows/release.yml` when a vX.Y.Z tag is pushed: a wheel, the
install scripts, constraints.txt, the browser extension as a zip, and `latest.json`:

    {"version": "0.7.0", "wheel": "research_brain-0.7.0-py3-none-any.whl", "notes": "...", "published": "..."}

The app looks for that file in its *update source*, in this order:

1. RB_UPDATE_SOURCE, or the "update_source" setting: a folder (a shared drive, OneDrive, Box, a network
   share: anything synced to disk) holding latest.json and the wheels, or an https URL of a latest.json.
2. Otherwise the GitHub repository's latest release. The repository is public, so anyone can read
   `releases/latest/download/latest.json` with no account; a GITHUB_TOKEN (or `gh` signed in) is only
   a fallback, through the API.

The app asks at most once a day (cached in the data folder; GET /api/update, app/updates_api.py) and
shows "Update" in the top bar when there's a newer version. RB_UPDATE_CHECK=0 turns it off.

Installing from the app (POST /api/update/install): the wheel and constraints.txt are downloaded first,
into the data folder's `update/`, then a small shell script (a .cmd on Windows) is started on its own and
research brain stops. The script waits for research brain's processes to end (Windows can't replace a
running research-brain.exe, and nothing should run from a half-replaced install), reinstalls with whatever
installed this one (uv, pipx or the private virtualenv from install.sh), and starts research brain again.
While it runs, `update/updating.json` exists, and `research-brain open` (app/launcher.py) waits for it
instead of starting the old, half-replaced copy. The next start reads how it went (`last_result`).
`research-brain update` does the same from a terminal. A source checkout is told to `git pull` instead.
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


LATEST_URL = f"https://github.com/{REPO}/releases/latest/download/latest.json"


def release_file(version: str, name: str) -> str:
    """A file attached to the vX.Y.Z GitHub release (public: no account needed)."""
    return f"https://github.com/{REPO}/releases/download/v{version}/{name}"


def _from_github() -> dict | None:
    r = requests.get(LATEST_URL, timeout=6)  # follows GitHub's redirect to the file itself
    if r.status_code == 200:
        info = r.json()
        v = str(info.get("version") or "")
        if parse(v):
            if info.get("wheel") and "://" not in info["wheel"]:
                info["wheel"] = release_file(v, info["wheel"])
            info.update(constraints=release_file(v, "constraints.txt"), source="github",
                        url=f"https://github.com/{REPO}/releases/tag/v{v}")
            return info
    return _from_github_api()


def _from_github_api() -> dict | None:
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
            info["constraints"] = f"{base}/constraints.txt"
            info["source"] = src
            return info
        if src:
            return _from_folder(Path(src).expanduser())
        return _from_github()
    except (OSError, ValueError, AttributeError, requests.RequestException):
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


# ---------------------------------------------------------------- installing from the app
class UpdateError(Exception):
    """Something a person can act on: no internet, no installer found, a broken download."""


STALE = dt.timedelta(minutes=15)  # an update that hasn't finished by then has died: stop waiting for it


def update_dir() -> Path:
    return paths.user_data_dir() / "update"


def _marker() -> Path:
    return update_dir() / "updating.json"


def in_progress() -> dict | None:
    """{"from", "to", "started"} while an update is installing, else None."""
    try:
        m = json.loads(_marker().read_text(encoding="utf-8"))
        started = dt.datetime.fromisoformat(m["started"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if dt.datetime.now(dt.timezone.utc) - started > STALE:
        return None
    return m


def wait_while_updating(limit: float = STALE.total_seconds()) -> bool:
    """Block while an update installs (app/launcher.py, before starting the server). True if one was running."""
    import time
    t0, waited = time.monotonic(), False
    while in_progress() and time.monotonic() - t0 < limit:
        waited = True
        time.sleep(0.5)
    return waited


def last_result() -> dict | None:
    """How the last update from the app went, once it has finished: {"ok", "from", "to", "error"?, "log"?}.
    None when there's nothing to tell (no update, still running, or already acknowledged)."""
    d = update_dir()
    try:
        pend = json.loads((d / "pending.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if in_progress():
        return None
    try:
        code = int((d / "exit-code").read_text(encoding="utf-8").strip() or "1")
    except (OSError, ValueError):
        code = None
    ok = code == 0 and __version__ == pend.get("to")
    out = {"ok": ok, "from": pend.get("from"), "to": pend.get("to"), "version": __version__}
    if not ok:
        try:
            tail = (d / "update.log").read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
        except OSError:
            tail = []
        out["log"] = "\n".join(tail)
        out["error"] = ("The update didn't finish (it may have been stopped)." if code is None else
                        f"The installer stopped with an error (code {code}). You're still on {__version__}.")
    return out


def acknowledge() -> None:
    """The app has shown last_result(): forget it."""
    for n in ("pending.json", "exit-code"):
        (update_dir() / n).unlink(missing_ok=True)


def find_program(name: str) -> str | None:
    """uv or pipx, also where their installers put them: an app opened from the Dock or the Start menu
    doesn't have the PATH a terminal has."""
    p = shutil.which(name)
    if p:
        return p
    home = Path.home()
    exe = name + (".exe" if os.name == "nt" else "")
    for d in (home / ".local" / "bin", home / ".cargo" / "bin", Path("/opt/homebrew/bin"), Path("/usr/local/bin")):
        if (d / exe).is_file():
            return str(d / exe)
    return None


def can_install(how: str | None = None) -> tuple[bool, str]:
    """(True, "") if the app can update itself, else (False, what to do instead)."""
    how = how or how_installed()
    if how == "checkout":
        return False, "This copy runs from a source checkout: update it with git pull."
    if how in ("uv", "pipx") and not find_program(how):
        return False, f"Couldn't find {how}, which installed research brain. Run the newer installer instead."
    return True, ""


def _download(url: str, dest: Path) -> None:
    tmp = dest.with_name(dest.name + ".part")
    try:
        with requests.get(url, timeout=30, stream=True) as r:
            if r.status_code != 200:
                raise UpdateError(f"Couldn't download {dest.name} (HTTP {r.status_code}).")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk)
        os.replace(tmp, dest)
    except requests.RequestException as e:
        tmp.unlink(missing_ok=True)
        raise UpdateError("Couldn't download the update. Check the internet connection and try again.") from e


def prepare(info: dict) -> dict:
    """Put the release's wheel (and constraints.txt) in update/, so the install itself needs no network for
    research brain's own files and a bad download is found while the app is still running. Returns info
    pointing at those local files."""
    d = update_dir()
    d.mkdir(parents=True, exist_ok=True)
    wheel = info.get("wheel") or ""
    if not wheel:
        raise UpdateError("This release has no installable file. Run the newer installer instead.")
    name = wheel.rsplit("/", 1)[-1]
    if not re.fullmatch(r"research_brain-[\w.+-]+\.whl", name):
        raise UpdateError(f"Unexpected file name in latest.json: {name}")
    for old in d.glob("*.whl"):
        old.unlink(missing_ok=True)
    local = d / name
    if "://" in wheel:
        _download(wheel, local)
    else:
        shutil.copyfile(wheel, local)
    with open(local, "rb") as f:
        if f.read(2) != b"PK":
            local.unlink(missing_ok=True)
            raise UpdateError("The downloaded update is damaged. Try again later.")
    cons = d / "constraints.txt"
    cons.unlink(missing_ok=True)
    src = info.get("constraints") or ""
    folder = info.get("source") or ""
    try:
        if src.startswith("https://"):
            _download(src, cons)
        elif folder and (Path(folder) / "constraints.txt").is_file():
            shutil.copyfile(Path(folder) / "constraints.txt", cons)
    except UpdateError:
        cons.unlink(missing_ok=True)  # the install still works without the pins
    return {**info, "wheel": str(local), "source": str(d)}


def relaunch_command(port: int) -> list[str]:
    """How to start research brain again after the install: like `research-brain open` starts it."""
    cmd = [sys.executable, "-m", f"{__package__ or 'app'}.cli", "serve", "--no-open", "--port", str(port)]
    if int(os.environ.get("RB_IDLE_EXIT", "0") or 0) > 0:
        cmd.append("--quit-when-closed")
    if os.environ.get("RB_VAULT"):
        cmd += ["--vault", os.environ["RB_VAULT"]]
    elif os.environ.get("RB_PROFILE"):
        cmd += ["--profile", os.environ["RB_PROFILE"]]
    return cmd


def _sh(cmd: list[str]) -> str:
    import shlex
    return " ".join(shlex.quote(c) for c in cmd)


def script(install: list[str], *, pids: list[int], port: int, relaunch: list[str], cwd: str, windows: bool) -> str:
    """The script that waits for research brain to stop, installs, and starts it again."""
    d = update_dir()
    log, code, marker, applog = d / "update.log", d / "exit-code", _marker(), paths.user_data_dir() / "app.log"
    hb = f"http://127.0.0.1:{port}/api/heartbeat"
    if windows:
        from subprocess import list2cmdline as q
        lines = ["@echo off", "rem research brain update (app/updates.py): wait, install, start again"]
        for i, pid in enumerate(pids):
            lines += [f":w{i}", f'tasklist /FI "PID eq {pid}" /NH 2>nul | findstr /C:" {pid} " >nul && (ping -n 2 127.0.0.1 >nul & goto w{i})']
        lines += [f'echo == updating >> "{log}"', f'{q(install)} >> "{log}" 2>&1', f'>"{code}" echo %ERRORLEVEL%',
                  f'cd /d "{cwd}"', f'start "" /B {q(relaunch)} >> "{applog}" 2>&1',
                  "set n=0", ":up", f"curl -fs -o NUL {hb} && goto done", "set /a n+=1", "if %n% GEQ 120 goto done",
                  "ping -n 2 127.0.0.1 >nul", "goto up", ":done", f'del /q "{marker}"']
        return "\r\n".join(lines) + "\r\n"
    lines = ["#!/bin/sh", "# research brain update (app/updates.py): wait, install, start again"]
    for pid in pids:
        # a process that has ended but whose parent hasn't collected it yet (the Mac window started this
        # server, and stays open) still answers kill -0: count it as gone ("Z" in ps)
        lines.append(f"n=0; while kill -0 {pid} 2>/dev/null && [ \"$(ps -o stat= -p {pid} 2>/dev/null | cut -c1)\" != Z ]"
                     " && [ $n -lt 1200 ]; do sleep 0.5; n=$((n+1)); done")
    lines += [f"echo \"== $(date)\" >> {_sh([str(log)])}", f"{_sh(install)} >> {_sh([str(log)])} 2>&1",
              f"echo $? > {_sh([str(code)])}", f"cd {_sh([cwd])}",
              f"nohup {_sh(relaunch)} >> {_sh([str(applog)])} 2>&1 < /dev/null &",
              "n=0; while [ $n -lt 240 ]; do",
              f"  if command -v curl >/dev/null 2>&1; then curl -fs -o /dev/null {hb} && break; else sleep 5; break; fi",
              "  sleep 0.5; n=$((n+1)); done", f"rm -f {_sh([str(marker)])}"]
    return "\n".join(lines) + "\n"


def start_install(info: dict, *, port: int, pids: list[int]) -> dict:
    """Start the update script on its own (it outlives this process) and mark the update as running. The
    caller then stops research brain. info: from prepare()."""
    how = how_installed()
    ok, why = can_install(how)
    if not ok:
        raise UpdateError(why)
    cmd = install_command(info, how)
    if not cmd:
        raise UpdateError("Couldn't work out how research brain was installed. Run the newer installer instead.")
    if how in ("uv", "pipx"):
        cmd[0] = find_program(how) or cmd[0]
    d = update_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "exit-code").unlink(missing_ok=True)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    state = {"from": __version__, "to": info["version"], "started": now}
    paths.write_json_atomic(d / "pending.json", state)
    paths.write_json_atomic(_marker(), state)
    windows = os.name == "nt"
    path = d / ("run-update.cmd" if windows else "run-update.sh")
    path.write_text(script(cmd, pids=pids, port=port, relaunch=relaunch_command(port), cwd=str(paths.PKG_DIR.parent),
                           windows=windows), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("RB_RESTART_FILE", "RB_PORT")}
    try:
        if windows:
            # a hidden console (not DETACHED_PROCESS), so the server it starts again gets no window either
            subprocess.Popen(["cmd", "/c", str(path)], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True, creationflags=0x00000200 | 0x08000000)
        else:
            subprocess.Popen(["/bin/sh", str(path)], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        _marker().unlink(missing_ok=True)
        (d / "pending.json").unlink(missing_ok=True)
        raise UpdateError(f"Couldn't start the update: {e}") from e
    return state
