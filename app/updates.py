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

The app asks each time it's opened, and about every hour while it stays open (what it heard is kept in the data
folder; GET /api/update, app/updates_api.py), and shows "Update" in the top bar when there's a newer version. A check
that failed (no internet) is tried again within minutes and never forgets a release already heard of.
RB_UPDATE_CHECK=0 turns it off.

Installing from the app (POST /api/update/install): the wheel and constraints.txt are downloaded first,
into the data folder's `update/`, and with uv the new version is tried out beside this one (try_out): one that can't
be installed or doesn't start is found out while this one still runs. Then app/update_run.py
is started on its own and research brain stops. It waits for research brain's processes to end (Windows can't replace a
running research-brain.exe, and nothing should run from a half-replaced install), reinstalls with whatever
installed this one (uv, pipx or the private virtualenv from install.sh), and starts research brain again, or puts the
version it had back when the new one doesn't run.
While it runs, `update/updating.json` exists (with the installer's process: one that died, with the computer
switched off in the middle, say, is seen to have died and blocks nothing), and `research-brain open` (app/launcher.py) waits for it
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
import threading
from pathlib import Path

import requests

from . import __version__, paths

REPO = "henrykanaskie/research-brain"
CHECK_EVERY = dt.timedelta(minutes=50)  # while it stays open (static/updates.js asks every hour)
OPEN_EVERY = dt.timedelta(minutes=1)    # each time it's opened, but not for every reload of the page
RETRY_EVERY = dt.timedelta(minutes=2)   # after a check that failed
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
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
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


_asking = threading.Lock()


def check(force: bool = False, opened: bool = False) -> dict:
    """{"current", "latest", "newer", "notes", "url", "source", "checked", "failed", "command"}. Asks the update source
    when what's kept is old (see CHECK_EVERY; opened: the app was just opened, OPEN_EVERY) or force. A check that fails
    answers with the release last heard of, and "failed"; "checked" is the last one that worked."""
    out = {"current": __version__, "latest": None, "newer": False, "notes": "", "url": "", "source": source() or "github",
           "checked": None, "failed": False, "command": "research-brain update", "enabled": enabled()}
    if not enabled():
        return out
    kept = _kept(out["source"])
    if not force and not _due(kept, opened):
        return _merge(out, kept)
    # one at a time: a page that's only waiting (an update installing) is answered from what's kept meanwhile
    if not _asking.acquire(blocking=force):
        return _merge(out, kept)
    try:
        return _merge(out, _keep(out["source"], fetch()))
    finally:
        _asking.release()


def _when(iso) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(iso)
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def _kept(src: str) -> dict:
    """What the last checks of this source heard: {"checked", "info"}, and "tried" when one has failed since."""
    try:
        k = json.loads(_cache_path().read_text(encoding="utf-8"))
        if k.get("source") != src:
            return {}
    except (OSError, ValueError, AttributeError):
        return {}
    if not isinstance(k.get("info"), dict):  # versions before 1.0.69 kept a failed check as if it had worked
        k.update(info=None, tried=k.get("tried") or k.get("checked"), checked=None)
    return k


def _due(kept: dict, opened: bool) -> bool:
    now = dt.datetime.now(dt.timezone.utc)

    def within(iso, limit: dt.timedelta) -> bool:  # (a time in the future is a clock that was wrong)
        t = _when(iso)
        return t is not None and dt.timedelta(0) <= now - t < limit
    every = OPEN_EVERY if opened else CHECK_EVERY
    if kept.get("tried"):
        return not within(kept["tried"], min(every, RETRY_EVERY))
    return not within(kept.get("checked"), every)


def _keep(src: str, info: dict | None) -> dict:
    stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    if info and parse(str(info.get("version", ""))):
        kept = {"checked": stamp, "source": src, "info": info}
    else:
        old = _kept(src)
        kept = {"checked": old.get("checked"), "source": src, "info": old.get("info"), "tried": stamp}
    try:
        paths.write_json_atomic(_cache_path(), kept)
    except OSError:
        pass
    return kept


def _merge(out: dict, kept: dict) -> dict:
    info = kept.get("info")
    out.update(checked=kept.get("checked"), failed=bool(kept.get("tried")))
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


def uv_python() -> str:
    """The Python uv installs research brain with. On an Arm Windows PC it's the Intel/AMD one, which Windows 11 runs as it
    is: some of what research brain needs has no Windows-on-Arm build, and compiling it there fails. install.ps1 does
    the same. (Asked of the registry: a program run that way is told the machine is AMD64.)"""
    if _windows() and _machine_arch() == "ARM64":
        return "cpython-3.12-windows-x86_64-none"
    return "3.12"


def _machine_arch() -> str:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment") as k:
            return str(winreg.QueryValueEx(k, "PROCESSOR_ARCHITECTURE")[0]).upper()
    except (ImportError, OSError):
        return ""


def install_command(info: dict, how: str | None = None) -> list[str] | None:
    how = how or how_installed()
    spec = install_spec(info, "[embeddings]" if _has_embeddings() else "")
    cons = []
    folder = info.get("source") or ""
    pinned_ok = how == "uv" or sys.version_info >= (3, 12)  # constraints.txt pins wheels built for 3.12+
    if pinned_ok and folder and Path(folder).is_dir() and (Path(folder) / "constraints.txt").is_file():
        cons = ["-c", str(Path(folder) / "constraints.txt")]
    if how == "uv":
        if cons and _spaced(cons[1]):
            cons[1] = "constraints.txt"  # uv splits a -c path at spaces: run it from that folder (install_cwd)
        return ["uv", "tool", "install", "--force", "--python", uv_python(), *cons, spec]
    if how == "pipx":
        return ["pipx", "install", "--force", *(["--pip-args", f'-c "{cons[1]}"'] if cons else []), spec]
    if how in ("venv", "pip"):
        return [sys.executable, "-m", "pip", "install", "--upgrade", *cons, spec]
    return None


def _spaced(path: str) -> bool:
    return any(c.isspace() for c in path)


def install_cwd(info: dict) -> str | None:
    """Where to run install_command from: the folder holding constraints.txt when its path has a space
    (~/Library/Application Support on a Mac, a shared "OneDrive - Company" folder), which uv 0.12 splits
    when it's given to -c, so the command names it relatively. None: anywhere."""
    folder = info.get("source") or ""
    if folder and Path(folder).is_dir() and (Path(folder) / "constraints.txt").is_file() and _spaced(folder):
        return str(Path(folder))
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
    pid = m.get("pid")
    if isinstance(pid, int) and pid > 0:
        return m if _is_installer(pid, started) else None
    if pid is None and m.get("starting"):  # its installer is being started right now
        return m if dt.datetime.now(dt.timezone.utc) - started < dt.timedelta(seconds=30) else None
    return m if _old_script_running() else None  # left by a version that didn't say which process


def _is_installer(pid: int, started: dt.datetime) -> bool:
    """Is the installer that wrote the marker still running (and not another program given its number since)?"""
    if not _alive(pid):
        return False
    if os.name == "nt":
        born = _born(pid)
        return born is None or abs((born - started).total_seconds()) < 300
    try:
        out = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, errors="replace", timeout=5).stdout
        return "update_run" in out or "run-update" in out or not out  # run-update.sh: 1.0.68 and before
    except (OSError, subprocess.SubprocessError):
        return True


def _born(pid: int) -> dt.datetime | None:
    import ctypes
    k = ctypes.windll.kernel32
    k.OpenProcess.restype = ctypes.c_void_p
    h = k.OpenProcess(0x1000, False, pid)
    if not h:
        return None
    try:
        t = [ctypes.c_ulonglong() for _ in range(4)]
        if not k.GetProcessTimes(ctypes.c_void_p(h), *(ctypes.byref(x) for x in t)):
            return None
        return dt.datetime(1601, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(microseconds=t[0].value // 10)
    finally:
        k.CloseHandle(ctypes.c_void_p(h))


_old_script: list = [0.0, False]  # when last asked, and the answer: asking Windows takes a second


def _old_script_running() -> bool:
    import time
    if time.monotonic() - _old_script[0] < 5 and _old_script[0]:
        return _old_script[1]
    try:
        if os.name == "nt":
            r = subprocess.run(["powershell", "-NoProfile", "-Command",
                                "@(Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | ? { $_.CommandLine -match 'run-update' }).Count"],
                               capture_output=True, text=True, errors="replace", timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            found = r.stdout.strip() not in ("", "0")
        else:
            found = subprocess.run(["pgrep", "-f", "run-update.sh"], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        found = True  # can't tell: wait, as before
    _old_script[:] = [time.monotonic(), found]
    return found


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
    name = wheel.rsplit("/", 1)[-1] if "://" in wheel else Path(wheel).name  # a Windows path has backslashes
    if not re.fullmatch(r"research_brain-[\w.+-]+\.whl", name):
        raise UpdateError(f"Unexpected file name in latest.json: {name}")
    for old in d.glob("*.whl"):
        if not old.name.startswith(f"research_brain-{__version__}-"):  # this version's is kept, to put it back if need be
            old.unlink(missing_ok=True)
    local = d / name
    if "://" in wheel:
        _download(wheel, local)
    else:
        shutil.copyfile(wheel, local)
    with open(local, "rb") as f:
        magic = f.read(2)
    if magic != b"PK":  # closed first: Windows can't delete an open file
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


def _windows() -> bool:
    return os.name == "nt"


def _window_file() -> Path:
    return paths.user_data_dir() / "window.json"


def _alive(pid: int) -> bool:
    """Whether process pid is still running."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def window_pid() -> int | None:
    """The process of research brain's own Windows window (app/winwindow.py writes window.json while it's
    open), if it's still running. The update waits for it too: Windows can't replace the install it runs from."""
    try:
        pid = int(json.loads(_window_file().read_text(encoding="utf-8")).get("pid") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    return pid if pid != os.getpid() and _alive(pid) else None


def reopen_command(port: int) -> list[str]:
    """How to open the window again after the install: what the Start menu entry runs."""
    cmd = [sys.executable, "-m", f"{__package__ or 'app'}.cli", "open", "--port", str(port)]
    if os.environ.get("RB_PROFILE") and not os.environ.get("RB_VAULT"):
        cmd += ["--profile", os.environ["RB_PROFILE"]]
    return cmd


def _console_python() -> str:
    exe = Path(sys.executable)
    c = exe.with_name("python.exe")
    return str(c) if exe.name.lower() == "pythonw.exe" and c.exists() else str(exe)


def _base_python() -> str:
    """The Python research brain's environment was made from: the update doesn't touch it."""
    real = Path(getattr(sys, "_base_executable", "") or "")
    bin_ = Path(sys.base_prefix) / "bin"
    for p in ([Path(sys.base_prefix) / "pythonw.exe", Path(sys.base_prefix) / "python.exe", real] if os.name == "nt" else
              [bin_ / f"python{sys.version_info[0]}.{sys.version_info[1]}", bin_ / "python3", real]):
        if p.name and p.is_file():
            return str(p)
    raise UpdateError("Couldn't find Python to run the update with. Run the newer installer instead.")


def _previous_dir() -> Path:
    return update_dir() / "previous"


def _pin_installed() -> str:
    """The folder with a constraints.txt of exactly what is installed now: putting this version back then needs nothing
    newer than it had (which might not work with it, or have to be compiled), and mostly nothing downloaded. "" if not."""
    import importlib.metadata as md
    pins = {}
    try:
        for x in md.distributions():
            name = (x.metadata["Name"] or "").strip()
            if re.fullmatch(r"[A-Za-z0-9._-]+", name) and x.version and name.lower().replace("_", "-") not in ("research-brain", "pip"):
                pins[name.lower()] = f"{name}=={x.version}"
        _previous_dir().mkdir(parents=True, exist_ok=True)
        (_previous_dir() / "constraints.txt").write_text("".join(f"{p}\n" for _, p in sorted(pins.items())), encoding="utf-8")
    except (OSError, KeyError, TypeError, ValueError):
        return ""
    return str(_previous_dir()) if pins else ""


def previous_command(how: str, pins: str = "") -> list[str] | None:
    """Puts this version back, from its GitHub release, if the new one turns out not to run. pins: _pin_installed()."""
    if not parse(__version__):
        return None
    name = f"research_brain-{__version__}-py3-none-any.whl"
    kept = update_dir() / name
    wheel = str(kept) if kept.is_file() else release_file(__version__, name)
    cmd = install_command({"version": __version__, "wheel": wheel, "source": pins}, how)
    if cmd and how in ("uv", "pipx"):
        cmd[0] = find_program(how) or cmd[0]
    return cmd


def try_out(info: dict, how: str) -> None:
    """Install the new version beside this one and ask it its version, before anything is stopped or replaced. uv keeps
    what it downloads, so the update itself is then quick and needs no internet. Raises UpdateError when it clearly
    can't be installed here; anything unclear doesn't stand in the way of updating."""
    uv = find_program("uv") if how == "uv" else None
    if not uv:
        return
    cons = ["-c", "constraints.txt"] if (Path(info.get("source") or "") / "constraints.txt").is_file() and Path(info["source"]).is_dir() else []
    cmd = [uv, "tool", "run", "--isolated", "--python", uv_python(), *cons, "--from", info["wheel"], "research-brain", "--version"]
    try:
        r = subprocess.run(cmd, cwd=info["source"] if cons else None, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as e:
        raise UpdateError("Downloading the update took longer than 10 minutes. Try again with a better connection.") from e
    except OSError:
        return
    said = (r.stderr or "") + (r.stdout or "")
    if r.returncode == 0 and info["version"] in said:
        return
    try:
        (update_dir() / "try-out.log").write_text(said, encoding="utf-8")
    except OSError:
        pass
    if re.search(r"error sending request|Failed to (download|fetch)|dns error|timed out|Connect", said):
        raise UpdateError("Couldn't download what the update needs. Check the internet connection and try again.")
    if re.search(r"No solution found|Failed to build|Failed to install|Traceback \(most recent", said):
        last = next((ln.strip() for ln in reversed(said.splitlines()) if ln.strip()), "")
        raise UpdateError(f"{info['version']} can't be installed on this computer, so nothing was changed. {last[:300]}")


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
    paths.write_json_atomic(_marker(), {**state, "starting": True})
    windows = _windows()
    reopen = None
    win = window_pid() if windows else None
    if win is not None:  # the window closes itself for the update (it watches in_progress()) and comes back after
        pids = [*pids, win] if win not in pids else pids
        reopen = reopen_command(port)
    env = {k: v for k, v in os.environ.items() if k not in ("RB_RESTART_FILE", "RB_PORT")}
    (d / "update.log").unlink(missing_ok=True)
    try:
        pins = _pin_installed()
        job = {"from": __version__, "to": info["version"], "pids": pids, "port": port, "install": cmd, "cwd": install_cwd(info),
               "folder": sys.prefix if sys.prefix != sys.base_prefix else "", "marker": str(_marker()),
               "applog": str(paths.user_data_dir() / "app.log"), "home": str(Path.home()),
               "check": [_console_python(), "-m", f"{__package__ or 'app'}.cli", "--version"],
               "serve": relaunch_command(port), "open": reopen, "previous": previous_command(how, pins),
               "previous_cwd": pins or None, "previous_unpinned": previous_command(how)}
        paths.write_json_atomic(d / "job.json", job)
        run = d / "update_run.py"
        shutil.copyfile(Path(__file__).with_name("update_run.py"), run)
        own = {"creationflags": 0x00000200 | 0x08000000} if windows else {"start_new_session": True}
        proc = subprocess.Popen([_base_python(), "-I", "-S", str(run), str(d / "job.json")], env=env, cwd=str(d),
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, **own)
        paths.write_json_atomic(_marker(), {**state, "pid": int(getattr(proc, "pid", 0) or 0) or None, "starting": True})
    except OSError as e:
        _marker().unlink(missing_ok=True)
        (d / "pending.json").unlink(missing_ok=True)
        raise UpdateError(f"Couldn't start the update: {e}") from e
    return state
