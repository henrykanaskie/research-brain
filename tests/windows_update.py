"""Updating the installed app on a real Windows machine, from its own Update button, and opening it whatever state an
update was left in. Run after research brain is installed (see tests/windows_installed.py, whose helpers this uses).

    python tests/windows_update.py real            the installed version updates to the newest release on GitHub
    python tests/windows_update.py local FOLDER    through made-up releases (tests/fake_releases.py): one that can't be
                                                   installed, one that doesn't start, killed half way, files in use
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import windows_installed as W  # noqa: E402
from windows_installed import DATA, SHOTS, api, check, failed, log_tail, open_app, processes, pdf, upload  # noqa: E402

UPD = DATA / "update"


def version() -> str:
    return api("GET", "/api/update", timeout=20)["current"]


def mine() -> list[dict]:
    return [p for p in processes() if "research_brain" in (p.get("CommandLine") or "") or "research-brain" in (p.get("CommandLine") or "")]


def dump(tag: str) -> None:
    out = SHOTS / f"{tag}.txt"
    parts = [f"{f.name}:\n{f.read_text(encoding='utf-8', errors='replace')[-6000:]}" for f in sorted(UPD.glob("*")) if f.is_file() and f.suffix in (".log", ".json", ".cmd", "") and f.stat().st_size < 400_000]
    parts.append("app.log:\n" + log_tail(60))
    wl = DATA / "window.log"
    parts.append("window.log:\n" + (wl.read_text(encoding="utf-8", errors="replace")[-3000:] if wl.exists() else "(none)"))
    parts.append("processes:\n" + json.dumps(mine(), indent=1, ensure_ascii=False))
    out.write_text("\n\n".join(parts), encoding="utf-8")
    print(f"----- {tag}\n" + "\n\n".join(parts) + "\n-----", flush=True)


def hwnd() -> int:
    import ctypes
    h = ctypes.windll.user32.FindWindowW(None, "research brain")
    return h if h and ctypes.windll.user32.IsWindowVisible(h) else 0


def wait_window(limit=60) -> int:
    t0 = time.time()
    while time.time() - t0 < limit:
        if hwnd():
            return hwnd()
        time.sleep(1)
    raise AssertionError("no window")


def update(limit=300) -> str:
    """Press Update (what the button sends) and wait for the new version to answer."""
    before = version()
    r = api("POST", "/api/update/install", {}, timeout=120)
    t0, down, seen = time.time(), None, []
    while time.time() - t0 < limit:
        try:
            s = api("GET", "/api/update", timeout=4)
            if s["current"] != before and not s["installing"]:
                return f"{before} -> {s['current']} in {time.time() - t0:.0f} s (away {time.time() - (down or t0):.0f} s)"
            if down and not s["installing"]:
                raise AssertionError(f"it came back as {s['current']}: {s.get('result')}")
        except (OSError, AssertionError) as e:
            if "came back" in str(e):
                raise
            down = down or time.time()
        n = sorted({f"{p['Name']}:{p['ProcessId']}" for p in mine()})
        if n != (seen[-1][1] if seen else None):
            seen.append((round(time.time() - t0), n))
        time.sleep(1)
    raise AssertionError(f"not back after {limit} s; asked for {r}; processes over time: {seen[-8:]}")


def start_fresh():
    """As after switching the computer on: nothing of it running."""
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process | ? { ($_.CommandLine -match 'research_brain|research-brain|run-update') -and $_.ProcessId -ne $PID -and $_.CommandLine -notmatch 'windows_update|windows_speed' } | % { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
                   capture_output=True)
    time.sleep(3)


def run_console_python() -> str:
    """The installed app's own python.exe (uv's launcher in the folder an update replaces)."""
    d = Path(os.environ.get("UV_TOOL_DIR") or Path(os.environ["APPDATA"]) / "uv" / "tools") / "research-brain" / "Scripts"
    real = Path(os.environ["RB_REAL_APPDATA"]) / "uv" / "tools" / "research-brain" / "Scripts" if os.environ.get("RB_REAL_APPDATA") else d
    for x in (d, real):
        if (x / "python.exe").exists():
            return str(x / "python.exe")
    raise AssertionError(f"no python.exe in {d}")


def offer(folder: str | None) -> None:
    """The release the app sees (it reads the folder each time it asks)."""
    (DATA).mkdir(parents=True, exist_ok=True)
    f = DATA / "settings.json"
    got = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    got["update_source"] = folder
    if folder is None:
        got.pop("update_source")
    f.write_text(json.dumps(got), encoding="utf-8")
    (DATA / "update-check.json").unlink(missing_ok=True)


def opened() -> str:
    start_fresh()
    t = open_app(updates=True)
    wait_window()
    return t


def result() -> dict:
    return api("GET", "/api/update", timeout=20)["result"]


def real() -> None:
    """The installed version updates itself to the newest release on GitHub."""
    want = []

    @check("opens while a newer release is out")
    def _():
        t = open_app(updates=True)
        wait_window()
        s = api("GET", "/api/update?force=true", timeout=30)
        assert s["newer"], s
        want.append(s["latest"])
        for i in range(3):
            upload(f"u{i}.pdf", pdf(f"A paper from before the update number {i}", [f"marker before{i}"]))
        return f"{t}; {s['current']}, newer {s['latest']}, can install {s['can_install']} {s['reason']}"

    @check("closes and opens again with the newer release known")
    def _():
        t = opened()
        time.sleep(8)  # the page asks about updates 4 s after it opens
        assert hwnd(), "the window went away"
        return t

    @check("updates")
    def _():
        r = update()
        assert version() == want[0]
        return r

    @check("opens after it, with the library as it was")
    def _():
        t = opened()
        assert version() == want[0] and len(api("GET", "/api/graph")["nodes"]) == 3
        return t
    dump("after-real")


def local(root: Path) -> None:
    """root holds made-up releases: good (9.9.9), good2 (9.9.12), cant (9.9.10, needs a package that doesn't exist),
    dies (9.9.11, installs but its server doesn't start), and this version's own wheel in own/."""
    first: list = []

    @check("opens while a newer release is out")
    def _():
        offer(str(root / "good"))
        t = open_app(updates=True)
        wait_window()
        s = api("GET", "/api/update?force=true", timeout=30)
        assert s["newer"] and s["latest"] == "9.9.9" and s["can_install"], s
        first.append(s["current"])
        for i in range(3):
            upload(f"u{i}.pdf", pdf(f"A paper from before the update number {i}", [f"marker before{i}"]))
        return t

    @check("opens as quickly when the update source can't be reached (no internet, a firewall)")
    def _():
        offer("https://10.255.255.1/latest.json")  # nobody answers there
        t = opened()
        time.sleep(8)
        assert hwnd() and api("GET", "/api/status", timeout=5)
        return t

    @check("a release that can't be installed here: refused with a reason, and nothing was stopped")
    def _():
        offer(str(root / "cant"))
        opened()
        pid = {p["ProcessId"] for p in mine()}
        try:
            api("POST", "/api/update/install", {}, timeout=300)
        except AssertionError as e:
            assert "can't be installed on this computer" in str(e), e
        else:
            raise AssertionError("it was let through")
        assert version() == first[0] and hwnd() and pid & {p["ProcessId"] for p in mine()}
        assert not (UPD / "updating.json").exists()

    @check("a release whose server doesn't start: this version is put back, and opens")
    def _():
        import shutil
        UPD.mkdir(parents=True, exist_ok=True)
        for w in (root / "own").glob("*.whl"):
            shutil.copy(w, UPD)
        offer(str(root / "dies"))
        opened()
        api("POST", "/api/update/install", {}, timeout=300)
        t0 = time.time()
        while time.time() - t0 < 400:
            try:
                if (UPD / "exit-code").exists() and not (UPD / "updating.json").exists() and version() == first[0] and "done" in (UPD / "update.log").read_text(encoding="utf-8", errors="replace"):
                    break
            except (OSError, AssertionError):
                pass
            time.sleep(2)
        else:
            raise AssertionError("not back")
        wait_window(90)
        r = result()
        assert r and not r["ok"], r
        return f"{time.time() - t0:.0f} s"
    dump("after-dies")

    @check("an update killed before it got anywhere (switched off, antivirus): opens at once, on this version")
    def _():
        api("POST", "/api/update/ack", {})
        offer(str(root / "good"))
        opened()
        api("POST", "/api/update/install", {}, timeout=300)
        kill_updater()
        t0 = time.time()
        start_fresh()
        assert (UPD / "updating.json").exists(), "the update finished before it could be killed"
        t = open_app(timeout=60, updates=True)
        wait_window()
        time.sleep(8)
        assert hwnd(), "the window closed itself"
        return f"{t}, version {version()}, {time.time() - t0:.0f} s in all"

    @check("a leftover marker from an older version's dead update: opens at once")
    def _():
        start_fresh()
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        for extra in ({}, {"pid": 4, "starting": True}):  # none; and a number another program has now
            (UPD / "updating.json").write_text(json.dumps({"from": "1.0.60", "to": "99.0.0", "started": now, **extra}), encoding="utf-8")
            t = open_app(timeout=60, updates=True)
            wait_window()
            time.sleep(6)
            assert hwnd(), "the window closed itself"
            start_fresh()
        (UPD / "updating.json").unlink(missing_ok=True)
        (UPD / "pending.json").unlink(missing_ok=True)
        return t

    @check("updates with its window open and one of its programs left running (an AI's tools server)")
    def _():
        offer(str(root / "good"))
        opened()
        holder = subprocess.Popen([run_console_python(), "-c", "import numpy, lxml.etree, time; time.sleep(900)"])
        time.sleep(3)
        r = update()
        assert version() == "9.9.9"
        assert holder.poll() is not None, "it was left running from the replaced folder"
        return r

    @check("its window comes back by itself, with the library as it was")
    def _():
        wait_window(90)
        time.sleep(6)
        assert hwnd()
        assert len(api("GET", "/api/graph")["nodes"]) == 3
        W.window_picture(hwnd()).save(SHOTS / "after-update.png")

    @check("it says it was updated, and the update left nothing behind")
    def _():
        left = [p for p in mine() if "update_run" in (p.get("CommandLine") or "") or "run-update" in (p.get("CommandLine") or "")]
        time.sleep(5)
        assert not (UPD / "updating.json").exists()
        left = [p for p in processes() if "update_run" in (p.get("CommandLine") or "")]
        assert not left, left
        log = (UPD / "update.log").read_text(encoding="utf-8", errors="replace")
        assert "done (0)" in log, log[-800:]
    dump("after-update")

    @check("updates again without a window (in the browser): the server comes back")
    def _():
        offer(str(root / "good2"))
        os.environ["RB_APP_WINDOW"] = "browser"
        try:
            start_fresh()
            open_app(updates=True)
            assert not hwnd()
            r = update()
            assert version() == "9.9.12"
        finally:
            os.environ.pop("RB_APP_WINDOW")
        return r

    @check("opens from the Start menu after all that, with the library as it was")
    def _():
        t = opened()
        assert version() == "9.9.12" and len(api("GET", "/api/graph")["nodes"]) == 3
        return t


def kill_updater() -> None:
    for _ in range(200):
        got = [p for p in processes() if "update_run" in (p.get("CommandLine") or "")]
        if got:
            for p in got:
                subprocess.run(["taskkill", "/F", "/PID", str(p["ProcessId"])], capture_output=True)
            return
        time.sleep(0.1)
    raise AssertionError("no updater was running")


def main() -> int:
    os.environ.pop("RB_UPDATE_SOURCE", None)
    print("home", W.HOME, "mode", sys.argv[1], flush=True)
    if sys.argv[1] == "real":
        real()
    else:
        local(Path(sys.argv[2]))
    if failed:
        dump("at-the-end")
    start_fresh()
    print("\n" + (f"{len(failed)} FAILED:\n  " + "\n  ".join(failed) if failed else "all passed"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
